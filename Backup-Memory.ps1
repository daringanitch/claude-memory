<#
.SYNOPSIS
    Back up the claude-memory PostgreSQL database to a compressed dump file.
.EXAMPLE
    .\Backup-Memory.ps1
    .\Backup-Memory.ps1 -OutputDir D:\backups
#>
param(
    [string]$OutputDir   = "$PSScriptRoot\backups",
    [string]$DbContainer = "claude-memory-db"
)

$timestamp = Get-Date -Format "yyyy-MM-ddTHH-mm-ss"
$dumpFile  = Join-Path $OutputDir "claude-memory-$timestamp.pgdump"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$running = docker ps --format "{{.Names}}" 2>$null | Where-Object { $_ -eq $DbContainer }
if (-not $running) {
    Write-Host "Starting DB service..."
    Push-Location $PSScriptRoot
    docker compose up -d db
    Pop-Location
    Start-Sleep -Seconds 5
}

Write-Host "Backing up claude-memory database..."
Write-Host "  Output: $dumpFile"

# Write dump inside container then copy out — avoids binary pipe encoding issues in PS 5.1
docker exec $DbContainer pg_dump -U claude -d memory --format=custom --compress=9 --file=/tmp/claudemem-backup.pgdump
if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump failed (exit $LASTEXITCODE)"; exit 1 }

docker cp "${DbContainer}:/tmp/claudemem-backup.pgdump" $dumpFile
if ($LASTEXITCODE -ne 0) { Write-Error "docker cp failed"; exit 1 }

docker exec $DbContainer rm /tmp/claudemem-backup.pgdump | Out-Null

$sizeKB = [math]::Round((Get-Item $dumpFile).Length / 1KB, 1)
Write-Host "Backup complete: $dumpFile ($sizeKB KB)"
Write-Host ""
Write-Host "To restore:  .\Restore-Memory.ps1 -DumpFile `"$dumpFile`""
