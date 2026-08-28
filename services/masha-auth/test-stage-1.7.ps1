param(
    [string]$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$Tools = 'G:\UDU-stage-1.0-tools'
)
# Native test runners write their normal progress to stderr on Windows PowerShell 5.
$ErrorActionPreference = 'Continue'
$python = Join-Path $Tools 'python-ssh\Scripts\python.exe'
$rust = Join-Path $Tools 'rust-1.75.0\rust-1.75.0-x86_64-pc-windows-msvc'
$cargo = Join-Path $rust 'cargo\bin\cargo.exe'
$rustc = Join-Path $rust 'rustc\bin\rustc.exe'
$vcvars = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
$cargoHome = Join-Path $Tools 'cargo-home'
$cargoTarget = Join-Path $Tools 'cargo-target-922372ba7'
$libclang = Join-Path $Tools 'python-libclang-15\clang\native'
$vcpkgRoot = Join-Path $Tools 'vcpkg'

Push-Location (Join-Path $Repo 'services\masha-auth')
try {
    $pythonOutput = & $python -m unittest discover -s test -p 'test_*.py' -v 2>&1 |
        ForEach-Object { $_.ToString() }
    if ($LASTEXITCODE -ne 0) {
        throw "Python acceptance tests failed: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$cmd = 'call "' + $vcvars + '" >nul' +
    ' && set "VCPKG_ROOT=' + $vcpkgRoot + '"' +
    ' && set "VCPKG_DEFAULT_TRIPLET=x64-windows-static"' +
    ' && set "VCPKG_DEFAULT_HOST_TRIPLET=x64-windows-static"' +
    ' && set "CARGO_HOME=' + $cargoHome + '"' +
    ' && set "CARGO_TARGET_DIR=' + $cargoTarget + '"' +
    ' && set "LIBCLANG_PATH=' + $libclang + '"' +
    ' && set "RUSTC=' + $rustc + '"' +
    ' && set "PATH=' + $cargoHome + '\bin;' + $rust + '\cargo\bin;' +
    $rust + '\rustc\bin;' + $rust + '\rustfmt-preview\bin;%PATH%"' +
    ' && cd /d "' + $Repo + '"' +
    ' && "' + $cargo + '" test --release --lib masha_ticket::tests -- --nocapture'
$rustOutput = & cmd.exe /d /s /c $cmd 2>&1 | ForEach-Object { $_.ToString() }
if ($LASTEXITCODE -ne 0) {
    Write-Output ($rustOutput -join [Environment]::NewLine)
    throw "Rust acceptance tests failed: $LASTEXITCODE"
}

$combined = ($pythonOutput + $rustOutput) -join [Environment]::NewLine
$checks = [ordered]@{
    '1 Active' = @('test_active_operator_receives_signed_ticket')
    '2 Blocked / expired' = @('test_blocked_operator_is_denied', 'test_expired_operator_is_denied')
    '3 Fail-closed' = @('authorization_server_unavailable_is_fail_closed')
    '4 Direct IP' = @('connection_gate_fails_closed_without_ticket_or_known_path')
    '5 Replay' = @('rejects_replayed_jti', 'test_repeated_start_and_session_id_do_not_duplicate_usage')
    '6 Wrong binding' = @('rejects_wrong_bindings')
    '7 Tamper' = @('rejects_tampered_payload', 'rejects_invalid_signature')
    '8 Lease revoke' = @('test_grant_revoke_stops_active_lease')
    '9 Heartbeat loss' = @('test_heartbeat_loss_finishes_with_server_duration')
    '10 Idempotency' = @('test_usage_accounting_is_incremental_and_idempotent', 'test_repeated_source_event_does_not_duplicate_grant')
    '11 Alternative grant' = @('test_overdue_payment_does_not_block_alternative_grants')
    '12 Concurrent sessions' = @('test_configured_concurrent_session_limit')
}

foreach ($entry in $checks.GetEnumerator()) {
    foreach ($pattern in $entry.Value) {
        if (-not $combined.Contains($pattern)) {
            throw "Acceptance evidence missing: $pattern"
        }
    }
    Write-Output ($entry.Key + '=PASS')
}
Write-Output 'PYTHON_TESTS=PASS'
Write-Output 'RUST_TESTS=PASS'
Write-Output 'STAGE_1_7=PASS'
