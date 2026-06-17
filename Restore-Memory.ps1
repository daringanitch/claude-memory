<#
.SYNOPSIS
    Restore the claude-memory PostgreSQL database from a backup dump file.
    WARNING: drops and recreates the memory database — all current data will be lost.
.EXAMPLE
    .\Restore-Memory.ps1 -DumpFile backups\claude-memory-2026-05-29T12-00-00.pgdump
#>
param(
    [Parameter(Mandatory)][string]$DumpFile,
    [string]$DbContainer = "claude-memory-db"
)

if (-not (Test-Path $DumpFile)) {
    Write-Error "File not found: $DumpFile"
    exit 1
}

Write-Host "WARNING: This will ERASE the current claude-memory database and restore from:"
Write-Host "   $DumpFile"
Write-Host ""
$confirm = Read-Host "Type YES to continue"
if ($confirm -ne "YES") {
    Write-Host "Aborted."
    exit 0
}

$running = docker ps --format "{{.Names}}" 2>$null | Where-Object { $_ -eq $DbContainer }
if (-not $running) {
    Write-Host "Starting DB service..."
    Push-Location $PSScriptRoot
    docker compose up -d db
    Pop-Location
    Start-Sleep -Seconds 5
}

Write-Host "Restoring database..."

docker exec $DbContainer psql -U claude -d postgres -c "DROP DATABASE IF EXISTS memory;"
if ($LASTEXITCODE -ne 0) { Write-Error "DROP DATABASE failed"; exit 1 }

docker exec $DbContainer psql -U claude -d postgres -c "CREATE DATABASE memory OWNER claude;"
if ($LASTEXITCODE -ne 0) { Write-Error "CREATE DATABASE failed"; exit 1 }

# Copy dump into container then restore — avoids binary pipe encoding issues in PS 5.1
docker cp $DumpFile "${DbContainer}:/tmp/claudemem-restore.pgdump"
if ($LASTEXITCODE -ne 0) { Write-Error "docker cp failed"; exit 1 }

docker exec $DbContainer pg_restore -U claude -d memory --no-owner --role=claude /tmp/claudemem-restore.pgdump
if ($LASTEXITCODE -ne 0) { Write-Error "pg_restore failed (exit $LASTEXITCODE)"; exit 1 }

docker exec $DbContainer rm /tmp/claudemem-restore.pgdump | Out-Null

Write-Host "Restore complete from: $DumpFile"
Write-Host ""
Write-Host "Restart the MCP server to reconnect:"
Write-Host "  docker compose restart mcp-server"
