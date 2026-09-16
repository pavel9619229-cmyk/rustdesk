param(
    [Parameter(Mandatory = $true)][string]$ReleaseDir,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$ExecutableName = 'masha-remote-operator.exe'
)

$ErrorActionPreference = 'Stop'
$ReleaseDir = (Resolve-Path $ReleaseDir).Path
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
[IO.Directory]::CreateDirectory($OutputDir) | Out-Null
$dataPath = Join-Path $OutputDir 'data.bin'
$metaPath = Join-Path $OutputDir 'app_metadata.toml'
$utf8 = [Text.UTF8Encoding]::new($false)

function Write-UInt32BE([IO.BinaryWriter]$Writer, [uint32]$Value) {
    $bytes = [BitConverter]::GetBytes($Value)
    if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($bytes) }
    $Writer.Write($bytes)
}

function Compress-Brotli([byte[]]$Data) {
    $stream = [IO.MemoryStream]::new()
    $brotli = [IO.Compression.BrotliStream]::new(
        $stream, [IO.Compression.CompressionLevel]::SmallestSize, $true
    )
    $brotli.Write($Data, 0, $Data.Length)
    $brotli.Dispose()
    $packed = $stream.ToArray()
    $stream.Dispose()
    return ,$packed
}$identifier = $utf8.GetBytes('rustdesk')
$fs = [IO.File]::Open($dataPath, [IO.FileMode]::Create, [IO.FileAccess]::Write)
$writer = [IO.BinaryWriter]::new($fs, $utf8, $false)
try {
    $writer.Write($identifier)
    $files = Get-ChildItem $ReleaseDir -Recurse -File | Sort-Object FullName
    foreach ($file in $files) {
        $rel = [IO.Path]::GetRelativePath($ReleaseDir, $file.FullName)
        $portablePath = '.\' + $rel
        $pathBytes = $utf8.GetBytes($portablePath)
        $raw = [IO.File]::ReadAllBytes($file.FullName)
        $packed = Compress-Brotli $raw
        $md5 = [Security.Cryptography.MD5]::Create()
        try { $digest = $md5.ComputeHash($raw) } finally { $md5.Dispose() }
        $md5Hex = [Convert]::ToHexString($digest).ToLowerInvariant()
        Write-UInt32BE $writer ([uint32]$pathBytes.Length)
        $writer.Write($pathBytes)
        Write-UInt32BE $writer ([uint32]$packed.Length)
        $writer.Write($packed)
        $writer.Write($utf8.GetBytes($md5Hex))
    }
    $writer.Write($identifier)
    $writer.Write($utf8.GetBytes('.\' + $ExecutableName))
} finally {
    $writer.Dispose()
}

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
[IO.File]::WriteAllText($metaPath, "timestamp = $timestamp`n", $utf8)
Write-Output "PORTABLE_DATA=PASS"
Write-Output "FILES=$($files.Count)"
Write-Output "DATA_BIN_BYTES=$((Get-Item $dataPath).Length)"