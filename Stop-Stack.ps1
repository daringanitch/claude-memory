#Requires -Version 5.1
<#
.SYNOPSIS
    Stop-Stack.ps1 - Graceful full workstation shutdown in reverse startup order.

.DESCRIPTION
    Shuts down each component gracefully (WM_CLOSE / SIGTERM), waits for clean exit,
    then force-kills only if the process does not exit within the timeout.

    Shutdown order (reverse of Start-Stack.ps1):
      Layer 4 : Claude (Desktop)
      Layer 3 : Obsidian
      Layer 2 : Docker Compose stack

    Docker Desktop itself is left running (it is a machine-level service).

.PARAMETER Down
    If specified, runs 'docker compose down' (removes containers) instead of
    'docker compose stop' (preserves containers for fast restart).

.PARAMETER GraceSeconds
    Seconds to wait for a process to exit after WM_CLOSE before force-killing.
    Default: 15.

.EXAMPLE
    .\Stop-Stack.ps1                # stop containers, preserve for fast restart
    .\Stop-Stack.ps1 -Down          # remove containers (clean state)
    .\Stop-Stack.ps1 -Down -Verbose
#>

[CmdletBinding()]
param(
    [switch] $Down,
    [int]    $GraceSeconds = 15,
    # Default to the directory this script lives in — works for any clone path.
    [string] $RepoRoot     = $PSScriptRoot
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

function Write-Skipped([string]$msg) {
    Write-Host "[ -- ] $msg (not running)" -ForegroundColor DarkGray
}

# ---- graceful close helper ---------------------------------------------------
#
# Sends WM_CLOSE to the main window of the named process and waits up to
# $GraceSeconds for it to exit. Force-kills if it does not comply.
# Targets only the first (parent) process when multiple PIDs share the name.

function Stop-Gracefully {
    param(
        [string] $Name,
        [string] $DisplayName = $Name
    )

    $procs = Get-Process -Name $Name -ErrorAction SilentlyContinue
    if (-not $procs) {
        Write-Skipped $DisplayName
        return
    }

    # For Electron apps there are many worker processes; CloseMainWindow on the
    # one with a main window is enough - children exit when the parent does.
    $main = $procs | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if (-not $main) { $main = $procs | Select-Object -First 1 }

    Write-Verbose "  Sending WM_CLOSE to $DisplayName (pid $($main.Id))..."
    $closed = $main.CloseMainWindow()

    if ($closed) {
        if ($main.WaitForExit($GraceSeconds * 1000)) {
            Write-OK "$DisplayName exited cleanly"
            return
        }
        Write-Warn "  $DisplayName did not exit within ${GraceSeconds}s - force killing..."
    } else {
        Write-Warn "  $DisplayName has no main window - force killing..."
    }

    # Force kill all processes with this name (workers too)
    Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if (-not (Get-Process -Name $Name -ErrorAction SilentlyContinue)) {
        Write-OK "$DisplayName force-killed"
    } else {
        Write-Warn "  $DisplayName may still have residual processes"
    }
}

# ---- Docker compose shutdown -------------------------------------------------

function Stop-ComposeStack {
    $composeFile = Join-Path $RepoRoot 'docker-compose.yml'

    if ($Down) {
        Write-Verbose '  Running: docker compose down'
        # Single call - compose resolves dependency order automatically.
        docker compose -f $composeFile down 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-OK 'Docker Compose stack brought down (containers removed)'
        } else {
            Write-Warn "  docker compose down exited $LASTEXITCODE - stack may need manual cleanup"
        }
    } else {
        Write-Verbose '  Running: docker compose stop'
        # Single call - compose stops all services in reverse dependency order.
        docker compose -f $composeFile stop 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-OK 'Docker Compose stack stopped (containers preserved)'
        } else {
            Write-Warn "  docker compose stop exited $LASTEXITCODE - containers may still be running"
        }
    }
}

# =============================================================================
# Shutdown sequence
# =============================================================================

$mode = if ($Down) { 'DOWN (containers will be removed)' } else { 'STOP (containers preserved)' }
Write-Host ""
Write-Host "Shutting down claude-memory stack - Docker mode: $mode" -ForegroundColor Cyan

# ---- Layer 4: Claude ---------------------------------------------------------

Write-Step 'Layer 4 - Claude'
Stop-Gracefully -Name 'claude' -DisplayName 'Claude'

# ---- Layer 3: Obsidian -------------------------------------------------------

Write-Step 'Layer 3 - Obsidian'
Stop-Gracefully -Name 'Obsidian' -DisplayName 'Obsidian'

# ---- Layer 2: Docker Compose stack -------------------------------------------

Write-Step 'Layer 2 - Docker Compose stack'

$stackRunning = $false
try {
    $psOut = docker compose -f (Join-Path $RepoRoot 'docker-compose.yml') ps --format json 2>$null
    if ($psOut) {
        $services = $psOut | ForEach-Object { $_ | ConvertFrom-Json }
        $stackRunning = ($services | Where-Object { $_.State -eq 'running' }).Count -gt 0
    }
} catch { }

if ($stackRunning) {
    Stop-ComposeStack
} else {
    Write-Skipped 'Docker Compose stack'
}

# =============================================================================

Write-Host ""
Write-Host "[DONE] claude-memory stack shut down." -ForegroundColor Green
Write-Host "       Docker Desktop is still running (machine-level service)." -ForegroundColor DarkGray
Write-Host "       To restart: .\Start-Stack.ps1" -ForegroundColor DarkGray
Write-Host ""
