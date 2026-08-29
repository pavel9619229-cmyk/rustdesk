& 'C:\ProgramData\Happ\hide-current-console.exe'
$ErrorActionPreference = 'SilentlyContinue'
$mutex = New-Object System.Threading.Mutex($false, 'Local\HappYandexDirectResident')
if (-not $mutex.WaitOne(0, $false)) {
    exit 0
}
try {
    while ($true) {
        & 'C:\ProgramData\Happ\ensure-yandex-direct-core.ps1'
        Start-Sleep -Seconds 60
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
