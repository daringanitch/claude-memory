#Requires -Version 5.1
<#
.SYNOPSIS
    Start-Stack.ps1 - Full AI workstation startup with layered health checks.

.DESCRIPTION
    Starts each component in dependency order and health-checks it before moving on.

      Layer 1 : Docker Desktop daemon
      Layer 2 : docker compose up (PostgreSQL + ollama + MCP server) -> ensures the
                qwen2.5:7b model is present -> verified via Test-StackHealth.ps1
      Layer 3 : Obsidian
      Layer 4 : Claude (Desktop - Store install)

    On any layer failure after retries: prints [FAIL] Layer N - <reason> and exits with
    that layer number as the exit code. Remaining layers are skipped.

.EXAMPLE
    .\Start-Stack.ps1
    .\Start-Stack.ps1 -Verbose
#>

[CmdletBinding()]
param(
    # Default to the directory this script lives in — works for any clone path.
    [string]$RepoRoot    = $PSScriptRoot,
    [string]$DockerExe   = 'C:\Program Files\Docker\Docker\Docker Desktop.exe',
    [string]$ObsidianExe = "$env:LOCALAPPDATA\Obsidian\Obsidian.exe",
    [string]$ClaudeAUMID  = 'Claude_pzs8sxrjxfjjc!Claude',
    # Skip the GPU override even if an NVIDIA docker runtime is detected
    # (forces the base CPU-only stack). Auto-detection is on by default.
    [switch]$NoGpu
)

$ErrorActionPreference = 'Continue'

# ---- output helpers ----------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "[....] $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "[ OK ] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Invoke-Fail([int]$layer, [string]$msg) {
    Write-Host ""
    Write-Host "[FAIL] Layer $layer - $msg" -ForegroundColor Red
    Write-Host "       Recovery: see OPERATIONS.md" -ForegroundColor Red
    exit $layer
}

# ---- retry engine ------------------------------------------------------------
#
# Calls $Test up to $Retries times, sleeping $IntervalSec between attempts.
# Returns normally if $Test returns $true. Calls Invoke-Fail on exhaustion.

function Wait-Until {
    param(
        [scriptblock] $Test,
        [string]      $Description,
        [int]         $Layer,
        [int]         $Retries     = 3,
        [int]         $IntervalSec = 5
    )
    for ($i = 1; $i -le $Retries; $i++) {
        $ok = $false
        try { $ok = [bool](& $Test) } catch { }
        if ($ok) { return }
        if ($i -lt $Retries) {
            Write-Warn "  [$i/$Retries] $Description not ready - retrying in ${IntervalSec}s..."
            Start-Sleep -Seconds $IntervalSec
        }
    }
    Invoke-Fail $Layer "$Description did not become healthy after $Retries attempts"
}

# -----------------------------------------------------------------------------
# Layer 1 - Docker Desktop
# Health check: docker info exits 0 (daemon running and reachable)
# Retries: 18 x 10s = up to 3 min (Docker Desktop is slow from cold)
# -----------------------------------------------------------------------------

Write-Step 'Layer 1 - Docker Desktop'

$dockerUp = $false
try { $null = docker info 2>&1; $dockerUp = ($LASTEXITCODE -eq 0) } catch { }

if (-not $dockerUp) {
    Write-Warn '  Docker daemon not running - launching Docker Desktop...'
    Start-Process -FilePath $DockerExe
} else {
    Write-Verbose '  Docker Desktop already running'
}

Wait-Until -Layer 1 -Description 'Docker daemon (docker info)' -Retries 18 -IntervalSec 10 -Test {
    $null = docker info 2>&1
    return $LASTEXITCODE -eq 0
}
Write-OK 'Docker daemon ready'

# -----------------------------------------------------------------------------
# Layer 2 - Docker Compose stack (MCP server + PostgreSQL + ollama)
# Brings up the compose project, then verifies via Test-StackHealth.ps1.
# Test-StackHealth.ps1 calls exit, so it must run in a child powershell.exe
# process to avoid terminating this session.
# Retries: 4 x 10s (compose is fast once the daemon is ready)
# -----------------------------------------------------------------------------

Write-Step 'Layer 2 - Docker Compose stack'

$composeFile  = Join-Path $RepoRoot 'docker-compose.yml'
$gpuCompose   = Join-Path $RepoRoot 'docker-compose.gpu.yml'
$healthScript = Join-Path $RepoRoot 'Test-StackHealth.ps1'

# Include the GPU override when an NVIDIA docker runtime is present (unless
# -NoGpu). COMPOSE_FILE governs the bare `docker compose` calls below; on
# non-NVIDIA hosts we use the portable base file (ollama on CPU).
$gpuPresent = $false
if (-not $NoGpu) {
    try { $gpuPresent = ((docker info --format '{{json .Runtimes}}' 2>$null) -match 'nvidia') } catch { }
}
$env:COMPOSE_PATH_SEPARATOR = ';'
if ($gpuPresent) {
    $env:COMPOSE_FILE = "$composeFile;$gpuCompose"
    Write-Verbose '  GPU detected - including docker-compose.gpu.yml'
} else {
    $env:COMPOSE_FILE = $composeFile
    Write-Warn '  No NVIDIA docker runtime (or -NoGpu) - ollama will run on CPU'
}

$null = docker compose up -d 2>&1
if ($LASTEXITCODE -ne 0) {
    Invoke-Fail 2 "docker compose up -d failed (exit code $LASTEXITCODE)"
}

# Ensure the distillation model is present in the in-stack ollama service. On a
# fresh ollama_models volume this pulls qwen2.5:7b (~4.7GB, one-time) so the
# Test-StackHealth model check below passes; idempotent thereafter.
$ollamaReady = $false
for ($i = 1; $i -le 30; $i++) {
    docker compose exec -T ollama ollama list 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ollamaReady = $true; break }
    Start-Sleep -Seconds 2
}
if ($ollamaReady) {
    $RequiredModel = 'qwen2.5:7b'
    $haveModel = docker compose exec -T ollama ollama list 2>&1 | Select-String -SimpleMatch $RequiredModel
    if (-not $haveModel) {
        Write-Warn "  Model $RequiredModel not present - pulling (one-time, ~4.7GB)..."
        docker compose exec -T ollama ollama pull $RequiredModel
    } else {
        Write-Verbose "  Model $RequiredModel present."
    }
} else {
    Write-Warn '  ollama service not ready after 60s - Test-StackHealth model check may fail.'
}

Write-Verbose '  Compose up issued - polling Test-StackHealth.ps1...'
Wait-Until -Layer 2 -Description 'PostgreSQL + ollama + MCP server (Test-StackHealth.ps1)' -Retries 4 -IntervalSec 10 -Test {
    # Child process: Test-StackHealth.ps1 calls exit 0/1 which would kill the
    # parent session if invoked directly with &.
    # Pass -ComposeFile explicitly: $PSScriptRoot is empty in child powershell.exe processes.
    # OllamaUrl points at the in-stack ollama service via its published host port.
    powershell.exe -NonInteractive -NoProfile -File $healthScript -ComposeFile $composeFile -OllamaUrl 'http://localhost:11737' | Out-Null
    return $LASTEXITCODE -eq 0
}
Write-OK 'Stack healthy (PostgreSQL + ollama + MCP server - all Test-StackHealth layers PASS)'

# -----------------------------------------------------------------------------
# Layer 3 - Obsidian
# Health check: process is running
# Retries: 3 x 4s (process check only - no HTTP endpoint)
# -----------------------------------------------------------------------------

Write-Step 'Layer 3 - Obsidian'

$obsProc = Get-Process -Name 'Obsidian' -ErrorAction SilentlyContinue
if ($obsProc) {
    Write-Verbose "  Obsidian already running (pid $($obsProc.Id))"
} else {
    # Redirect stdout/stderr to prevent Obsidian's Electron update-checker output
    # from bleeding into the parent console after the script completes.
    Start-Process -FilePath $ObsidianExe `
        -RedirectStandardOutput "$env:TEMP\obsidian-stdout.log" `
        -RedirectStandardError  "$env:TEMP\obsidian-stderr.log" `
        -NoNewWindow
}

Wait-Until -Layer 3 -Description 'Obsidian process' -Retries 3 -IntervalSec 4 -Test {
    return $null -ne (Get-Process -Name 'Obsidian' -ErrorAction SilentlyContinue)
}
Write-OK 'Obsidian running'

# -----------------------------------------------------------------------------
# Layer 4 - Claude (Desktop - Store install)
# Must be launched via AUMID: direct .exe calls into WindowsApps are ACL-blocked.
# IMPORTANT: the Claude Code CLI (claude.exe in .local\bin) shares the process
# name 'claude'. Distinguish the Desktop app by MainWindowHandle -ne 0 (GUI app
# has a window; the headless CLI does not).
# Retries: 4 x 5s (Store app launch can be slower than a regular .exe)
# -----------------------------------------------------------------------------

Write-Step 'Layer 4 - Claude'

$claudeDesktop = Get-Process -Name 'claude' -ErrorAction SilentlyContinue |
                 Where-Object { $_.MainWindowHandle -ne 0 } |
                 Select-Object -First 1

if ($claudeDesktop) {
    Write-Verbose "  Claude Desktop already running (pid $($claudeDesktop.Id))"
} else {
    Start-Process "shell:AppsFolder\$ClaudeAUMID"
}

Wait-Until -Layer 4 -Description 'Claude Desktop (main window)' -Retries 4 -IntervalSec 5 -Test {
    $null -ne (Get-Process -Name 'claude' -ErrorAction SilentlyContinue |
               Where-Object { $_.MainWindowHandle -ne 0 })
}
Write-OK 'Claude Desktop running'

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------

Write-Host ""
Write-Host "[DONE] Full stack is up and healthy." -ForegroundColor Green
Write-Host "       MCP server : http://localhost:3333" -ForegroundColor DarkGray
Write-Host "       Web UI     : http://localhost:3333/ui" -ForegroundColor DarkGray
Write-Host "       Ollama     : http://localhost:11737 (in-stack)" -ForegroundColor DarkGray
Write-Host ""
