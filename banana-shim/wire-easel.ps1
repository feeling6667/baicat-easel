# Point EASEL .env at the local banana-shim. Backs up first; -Revert restores.
# ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# and multibyte comments there turn into parse errors.
[CmdletBinding()]
param(
    [string]$EaselRoot = '',
    [string]$Port = '8790',
    [string]$Model = 'nano-banana',
    [switch]$Revert
)

if (-not $EaselRoot) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $EaselRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$envFile = Join-Path $EaselRoot '.env'
if (-not (Test-Path $envFile)) { throw "not found: $envFile" }

if ($Revert) {
    $bak = Get-ChildItem -Path $EaselRoot -Filter '.env.bak.before-banana-shim-*' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $bak) { throw 'no backup to restore' }
    Copy-Item $bak.FullName $envFile -Force
    Write-Host "restored: $($bak.Name)"
    return
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bakFile = Join-Path $EaselRoot ".env.bak.before-banana-shim-$stamp"
Copy-Item $envFile $bakFile -Force
Write-Host "backup: $bakFile"

$want = [ordered]@{
    'IMG_BASE_URL' = "http://127.0.0.1:$Port/v1"
    'IMG_MODEL'    = $Model
    'IMG_API_KEY'  = 'local-banana-shim'
    'IMG_NO_PROXY' = '1'
}

$seen = @{}
$outLines = foreach ($line in (Get-Content $envFile)) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
        $key = $matches[1]
        if ($want.Contains($key)) {
            $seen[$key] = $true
            "$key=$($want[$key])"
            continue
        }
    }
    $line
}

foreach ($key in $want.Keys) {
    if (-not $seen.ContainsKey($key)) { $outLines += "$key=$($want[$key])" }
}

# Write without BOM: a BOM would corrupt the first KEY= line for parsers.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envFile, [string[]]$outLines, $utf8NoBom)

Write-Host 'wrote:'
$want.Keys | ForEach-Object { Write-Host ("  " + $_ + "=" + $want[$_]) }
Write-Host ''
Write-Host 'revert: powershell -File wire-easel.ps1 -Revert'
