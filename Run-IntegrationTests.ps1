<#
.SYNOPSIS
    Run the tests_integration/ suite against the live pgvector container.

.DESCRIPTION
    Spins up a one-shot `docker compose run` against the mcp-server image with
    the integration test directory and init.sql mounted in. A temp database
    `memory_test_<pid>` is created from init.sql, exercised, and dropped at
    the end of the run.

    The existing claude-memory-db service must already be up (or `docker compose
    up -d db` will be invoked). Production data is untouched — all SQL goes to
    the temp DB.

.EXAMPLE
    .\Run-IntegrationTests.ps1
    .\Run-IntegrationTests.ps1 -PytestArgs '-v -k test_save_memory'
#>
[CmdletBinding()]
param(
    [string]$PytestArgs = '-v'
)

$repoRoot = $PSScriptRoot
Push-Location $repoRoot
try {
    # Ensure db service is up — tests need it
    cmd /c "docker compose up -d db 2>&1" | Out-Null

    # Wait for db healthy (compose's healthcheck handles the polling)
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $status = docker inspect --format '{{.State.Health.Status}}' claude-memory-db 2>$null
        if ($status -eq 'healthy') { break }
        Start-Sleep -Seconds 2
    }
    if ($status -ne 'healthy') {
        Write-Error "claude-memory-db did not reach 'healthy' within 30s (last: $status)"
        exit 1
    }

    # One-shot container: mount tests + init.sql, install pytest + numpy, run.
    # numpy is already in the image (sentence-transformers dep), but pytest is not.
    $cmd = "pip install --quiet pytest && pytest /app/tests_integration $PytestArgs"
    cmd /c "docker compose run --rm -T --no-deps -v `"${repoRoot}/tests_integration:/app/tests_integration:ro`" -v `"${repoRoot}/init.sql:/app/init.sql:ro`" mcp-server sh -c `"$cmd`""
    $exit = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exit
