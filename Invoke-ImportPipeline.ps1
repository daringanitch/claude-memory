<#
.SYNOPSIS
    Run the full claude-memory import pipeline (4 steps + optional profile generation).
    Windows equivalent of import-cron.sh. Suitable for Windows Task Scheduler.
.PARAMETER OllamaUrl
    Ollama-compatible API endpoint for the distillation model.
    Defaults to the in-stack ollama service. Explicit -OllamaUrl always wins.
.PARAMETER Verbosity
    Output level for the Python pipeline scripts:
      Critical  — ERROR only (quietest). Long-running steps appear silent
                  between the always-on step-start / step-end markers.
      Standard  — ERROR + WARNING (medium). Hides per-session INFO chatter
                  but keeps anomalies visible.
      Detailed  — ERROR + WARNING + INFO (default). Full per-session
                  heartbeat output. Best for first-time runs and debugging.

    All levels show pipeline-step start/end markers with elapsed time, so
    you always know which step is active and how long it has been running.
.PARAMETER ClaudeProjects
    Path to your Claude Code session history. Default %USERPROFILE%\.claude\projects.
.PARAMETER LogFile
    Full path to the pipeline log. Default %TEMP%\claude-memory-import.log
    (UTF-8, no BOM).
.EXAMPLE
    .\Invoke-ImportPipeline.ps1
    .\Invoke-ImportPipeline.ps1 -Verbosity Standard
    .\Invoke-ImportPipeline.ps1 -Verbosity Critical
    .\Invoke-ImportPipeline.ps1 -OllamaUrl "http://host.docker.internal:11434/v1"
#>
param(
    [string]$OllamaUrl,
    [ValidateSet('Critical', 'Standard', 'Detailed')]
    [string]$Verbosity = 'Detailed',
    [string]$ClaudeProjects = "$env:USERPROFILE\.claude\projects",
    [string]$LogFile        = "$env:TEMP\claude-memory-import.log",
    # Skip the GPU override even if an NVIDIA docker runtime is detected (forces
    # the base CPU-only stack). Auto-detection is on by default.
    [switch]$NoGpu
)

# UTF-8 output encoding — makes PowerShell correctly decode Docker's UTF-8
# stdout when piped through cmd /c "... 2>&1". Without this, multi-byte chars
# (→, —, etc.) are decoded using the system OEM code page and appear garbled.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# OllamaUrl default: the in-stack `ollama` compose service, reached over the
# compose network (the pipeline Python runs via `docker compose run`, which joins
# that network). Explicit -OllamaUrl always wins — pass a host.docker.internal
# URL to target a host-side Ollama instance instead.
if (-not $OllamaUrl) {
    $OllamaUrl = "http://ollama:11434/v1"
}

# Map verbosity to Python logging level. Honored by each pipeline script
# via the LOGLEVEL env var (passed through to docker compose run -e).
$LogLevel = switch ($Verbosity) {
    'Critical' { 'ERROR' }
    'Standard' { 'WARNING' }
    'Detailed' { 'INFO' }
}

function Log {
    param([string]$msg)
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')  $msg"
    Write-Host $line
    # UTF-8 (no BOM) via .NET API. Avoids PS 5.1's Add-Content default of
    # ASCII / Out-File default of UTF-16 LE BOM, both of which corrupt the
    # log when combined with Tee-Object output and break grep/tail tools.
    [System.IO.File]::AppendAllText($LogFile, "$line`r`n", [System.Text.UTF8Encoding]::new($false))
}

# Invoke a native command and tee combined stdout+stderr to the log without
# (a) PowerShell 5.1 wrapping stderr lines as NativeCommandError records, and
# (b) Tee-Object writing UTF-16 LE BOM. Solution to (a): let cmd.exe do the
# 2>&1 merge before PowerShell sees the stream. Solution to (b): replace
# Tee-Object with a manual fan-out that uses .NET's UTF-8-no-BOM appender.
function Invoke-Tee {
    param([Parameter(Mandatory)][string]$Command)
    $enc = [System.Text.UTF8Encoding]::new($false)
    cmd /c "$Command 2>&1" | ForEach-Object {
        Write-Host $_
        [System.IO.File]::AppendAllText($LogFile, "$_`r`n", $enc)
    }
}

# Run a pipeline step with always-on start/end markers + elapsed time.
# These markers print regardless of -Verbosity so the user always sees
# which step is running and how long it took. The inner Python output
# is silenced/shown according to LOGLEVEL.
function Invoke-Step {
    param(
        [int]$Number,
        [string]$Name,
        [string]$Command
    )
    Log "Step $Number - $Name - started"
    $stepStart = Get-Date
    Invoke-Tee $Command
    $elapsed = (Get-Date) - $stepStart
    $ts = "{0:mm}m{0:ss}s" -f $elapsed
    Log "Step $Number - $Name - complete ($ts)"
}

Log "=== Import pipeline started (verbosity: $Verbosity -> LOGLEVEL=$LogLevel) ==="
$pipelineStart = Get-Date

# Include the GPU override when an NVIDIA docker runtime is present (and not
# opted out). COMPOSE_FILE (absolute paths) governs every `docker compose` call
# below, including the cmd /c children. On non-NVIDIA hosts we fall through to
# the portable base file (ollama on CPU).
$baseCompose = Join-Path $PSScriptRoot 'docker-compose.yml'
$gpuCompose  = Join-Path $PSScriptRoot 'docker-compose.gpu.yml'
$gpuPresent  = $false
if (-not $NoGpu) {
    try { $gpuPresent = ((docker info --format '{{json .Runtimes}}' 2>$null) -match 'nvidia') } catch { }
}
$env:COMPOSE_PATH_SEPARATOR = ';'
if ($gpuPresent) {
    $env:COMPOSE_FILE = "$baseCompose;$gpuCompose"
    Log "GPU detected - using docker-compose.yml + docker-compose.gpu.yml"
} else {
    $env:COMPOSE_FILE = $baseCompose
    Log "No NVIDIA docker runtime (or -NoGpu) - using base compose (ollama on CPU)"
}

# Ensure stack is up
Push-Location $PSScriptRoot
cmd /c "docker compose up -d 2>&1" | Out-Null
Start-Sleep -Seconds 5

# Wait for the in-stack ollama server to accept connections (the model
# ensure/pull below needs it). `docker compose up -d` returns once containers
# start, not once ollama is ready, so poll briefly.
$ollamaReady = $false
foreach ($i in 1..30) {
    cmd /c "docker compose exec -T ollama ollama list 2>&1" | Out-Null
    if ($LASTEXITCODE -eq 0) { $ollamaReady = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ollamaReady) {
    Log "WARNING: ollama service not ready after 60s - distill/behavioral steps may fail."
}

# Ensure the distillation model is present in the ollama volume. First run on a
# fresh volume pulls it (~4.7GB, one-time); idempotent thereafter.
# NOTE: model name hardcoded to match the Python scripts' default until the
# -Model wiring (deferred) parameterizes it end to end.
$RequiredModel = 'qwen2.5:7b'
$haveModel = cmd /c "docker compose exec -T ollama ollama list 2>&1" | Select-String -SimpleMatch $RequiredModel
if ($ollamaReady -and -not $haveModel) {
    Log "Model $RequiredModel not in ollama volume - pulling (one-time, ~4.7GB)..."
    Invoke-Tee "docker compose exec -T ollama ollama pull $RequiredModel"
} elseif ($haveModel) {
    Log "Model $RequiredModel present in ollama volume."
}

# TQDM_DISABLE=1 — silences sentence-transformers' per-batch progress bars,
# which PowerShell 5.1 can't render correctly (the Unicode block characters
# come out as garbled `Γûêúêûúêû` runs). Belt-and-suspenders alongside the
# show_progress_bar=False kwarg in each script's encode() call.
# LOGLEVEL=$LogLevel — controls Python script verbosity per -Verbosity flag.

$envFlags = "-e TQDM_DISABLE=1 -e LOGLEVEL=$LogLevel"

Invoke-Step 1 "Import" "docker compose --progress quiet run --rm -T $envFlags -v `"${ClaudeProjects}:/root/.claude/projects:ro`" -v `"$PSScriptRoot/import_memories.py:/app/import_memories.py:ro`" mcp-server python /app/import_memories.py --claude-code"

Invoke-Step 2 "Distill" "docker compose --progress quiet run --rm -T $envFlags -e `"OLLAMA_URL=$OllamaUrl`" -v `"$PSScriptRoot/distill_sessions.py:/app/distill_sessions.py:ro`" mcp-server python /app/distill_sessions.py"

Invoke-Step 3 "Extract behavioral signals" "docker compose --progress quiet run --rm -T $envFlags -v `"${ClaudeProjects}:/root/.claude/projects:ro`" -v `"$PSScriptRoot/extract_signals.py:/app/extract_signals.py:ro`" mcp-server python /app/extract_signals.py"

Invoke-Step 4 "Behavioral pass" "docker compose --progress quiet run --rm -T $envFlags -e `"OLLAMA_URL=$OllamaUrl`" -v `"${ClaudeProjects}:/root/.claude/projects:ro`" -v `"$PSScriptRoot/behavioral_pass.py:/app/behavioral_pass.py:ro`" mcp-server python /app/behavioral_pass.py"

Pop-Location

# Step 5: Generate user profile (optional — requires local venv)
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Log "Step 5 - Generate user profile - started"
    $stepStart = Get-Date
    Invoke-Tee "`"$venvPython`" `"$PSScriptRoot\generate_user_profile.py`""
    $elapsed = (Get-Date) - $stepStart
    Log "Step 5 - Generate user profile - complete ($('{0:mm}m{0:ss}s' -f $elapsed))"
} else {
    Log "Step 5 - Generate user profile - skipped (no venv at $venvPython)"
}

$totalElapsed = (Get-Date) - $pipelineStart
Log "=== Import pipeline finished (total $('{0:hh}h{0:mm}m{0:ss}s' -f $totalElapsed)) ==="
Write-Host ""
Write-Host "Log: $LogFile"
