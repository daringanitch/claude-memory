<#
.SYNOPSIS
    Health check for the claude-memory stack.

.DESCRIPTION
    Verifies the four layers required for the MCP server to function:
      1. Docker compose services (db + mcp-server) up & healthy
      2. MCP/REST server /health endpoint
      3. In-stack Ollama service (port 11737) reachable with qwen2.5:7b present
      4. get_stats round-trip via REST (DB query path)

    Exits 0 if all layers pass, 1 otherwise. Safe to run repeatedly.

.EXAMPLE
    .\Test-StackHealth.ps1
    .\Test-StackHealth.ps1 -Verbose
#>

[CmdletBinding()]
param(
    [string]$ComposeFile = (Join-Path $PSScriptRoot 'docker-compose.yml'),
    [string]$ServerUrl   = 'http://localhost:3333',
    [string]$OllamaUrl,
    [string]$RequiredModel = 'qwen2.5:7b'
)

# OllamaUrl default: the in-stack `ollama` service via its published host port
# (127.0.0.1:11737). Explicit -OllamaUrl always wins — pass a Msty/host URL
# (e.g. http://localhost:10000) to health-check a host-side instance instead.
if (-not $OllamaUrl) {
    $OllamaUrl = "http://localhost:11737"
}

$ErrorActionPreference = 'Continue'
$results = @()
$fail = $false

function Add-Result($layer, $ok, $detail) {
    $script:results += [pscustomobject]@{
        Layer  = $layer
        Status = if ($ok) { 'PASS' } else { 'FAIL' }
        Detail = $detail
    }
    if (-not $ok) { $script:fail = $true }
}

Write-Host "claude-memory stack health check" -ForegroundColor Cyan
Write-Host "Compose file: $ComposeFile`n"

# --- Layer 1: Docker compose services ---
try {
    $psOut = docker compose -f $ComposeFile ps --format json 2>$null
    if (-not $psOut) { throw "no compose output (stack down?)" }
    $services = $psOut | ForEach-Object { $_ | ConvertFrom-Json }
    foreach ($svc in @('claude-memory-db','claude-memory-mcp')) {
        $s = $services | Where-Object { $_.Name -eq $svc }
        if ($null -eq $s) {
            Add-Result "docker:$svc" $false "container missing"
        } else {
            $healthy = ($s.Health -eq 'healthy') -or ($s.State -eq 'running' -and -not $s.Health)
            Add-Result "docker:$svc" $healthy "$($s.State) / health=$($s.Health)"
        }
    }
} catch {
    Add-Result 'docker:compose' $false $_.Exception.Message
}

# --- Layer 2: MCP/REST /health ---
try {
    $h = Invoke-RestMethod "$ServerUrl/health" -TimeoutSec 5
    $ok = ($h.status -eq 'ok') -and ($h.db -eq 'ok')
    Add-Result 'server:/health' $ok "status=$($h.status) db=$($h.db) err=$($h.db_error)"
} catch {
    Add-Result 'server:/health' $false $_.Exception.Message
}

# --- Layer 3: Ollama + required model ---
try {
    $tags = Invoke-RestMethod "$OllamaUrl/api/tags" -TimeoutSec 5
    $hasModel = $tags.models | Where-Object { $_.name -eq $RequiredModel }
    Add-Result 'ollama:tags' ($null -ne $hasModel) "model '$RequiredModel' $(if ($hasModel){'present'}else{'MISSING'})"
} catch {
    Add-Result 'ollama:tags' $false $_.Exception.Message
}

# --- Layer 4: REST round-trip (exercises DB query path) ---
# Note: /api/stats returns aggregate counts + storage only. Session distill
# counts are MCP-only (via get_stats tool), not exposed on REST.
try {
    $stats = Invoke-RestMethod "$ServerUrl/api/stats" -TimeoutSec 10
    $ok = ($null -ne $stats.active) -and ($null -ne $stats.projects)
    $detail = "active=$($stats.active) deleted=$($stats.deleted) projects=$($stats.projects) storage_mb=$($stats.storage_mb)"
    Add-Result 'server:/api/stats' $ok $detail
} catch {
    Add-Result 'server:/api/stats' $false $_.Exception.Message
}

# --- Report ---
Write-Host ""
$results | Format-Table -AutoSize | Out-String | Write-Host

if ($fail) {
    Write-Host "RESULT: FAIL - CTX-mem stack is not live. If shut down intentionally, run .\Start-Stack.ps1 to restart." -ForegroundColor Red
    Write-Host "               For unexpected failures see OPERATIONS.md sec 3 and sec 10." -ForegroundColor DarkGray
    exit 1
} else {
    Write-Host "RESULT: PASS - CTX-mem stack is live." -ForegroundColor Green
    exit 0
}
