param(
    [string]$Python = "G:\UDU-stage-1.0-tools\python-ssh\Scripts\python.exe"
)

$ErrorActionPreference = 'Stop'
$serviceRoot = $PSScriptRoot
$serviceFile = Join-Path $serviceRoot 'masha_auth.py'

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = 'python'
}

$requiredTests = @(
    'test_postpaid_one_hour_is_exactly_one_ruble_and_idempotent',
    'test_postpaid_heartbeat_loss_uses_server_duration',
    'test_postpaid_warning_and_blocking_are_server_driven',
    'test_blocked_postpaid_account_allows_alternative_grants',
    'test_settle_resets_postpaid_debt',
    'test_access_status_http_endpoint',
    'test_stage_one_access_grants_are_preserved_by_postpaid_migration'
)

$previousServiceFile = $env:MASHA_AUTH_SERVICE_FILE
$env:MASHA_AUTH_SERVICE_FILE = $serviceFile

try {
    Push-Location $serviceRoot
    $testCommand = '"' + $Python + '" -m unittest discover -s test ' +
        '-p "test_*.py" -v 2>&1'
    $rawTestOutput = & cmd.exe /d /s /c $testCommand
    $exitCode = $LASTEXITCODE
    $testOutput = $rawTestOutput | Out-String
    Write-Host $testOutput
    if ($exitCode -ne 0) {
        throw "Python tests failed with exit code $exitCode"
    }
    foreach ($testName in $requiredTests) {
        if ($testOutput -notmatch [regex]::Escape($testName)) {
            throw "Acceptance evidence is missing: $testName"
        }
    }
}
finally {
    Pop-Location
    $env:MASHA_AUTH_SERVICE_FILE = $previousServiceFile
}

Write-Host 'Tariff 1 RUB/hour: PASS'
Write-Host 'Server duration and heartbeat closure: PASS'
Write-Host 'Idempotency and alternative grants: PASS'
Write-Host 'Due/grace, T-10 warning and blocking: PASS'
Write-Host 'GET /v1/access/status: PASS'
Write-Host 'Stage 1 migration safety: PASS'
Write-Host 'STAGE_2_0=PASS'
