param(
    [string]$LogFile = (Join-Path (Split-Path -Parent $PSScriptRoot) ".tmp-cloudflared-http2-live.log")
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$service = Get-CimInstance Win32_Service -Filter "Name='cloudflared'"
if (-not $service -or -not $service.PathName) {
    Write-Error "cloudflared service or token was not found."
}

$tokenMatch = [Regex]::Match($service.PathName, '--token\s+(\S+)')
if (-not $tokenMatch.Success) {
    Write-Error "Could not extract tunnel token from cloudflared service."
}

$token = $tokenMatch.Groups[1].Value
$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflaredExe)) {
    Write-Error "cloudflared.exe was not found at $cloudflaredExe"
}

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -match '--protocol http2' -and $_.CommandLine -match [Regex]::Escape($token)
}

if ($existing) {
    Write-Host "An http2 cloudflared connector is already running."
    $existing | Select-Object ProcessId, CommandLine | Format-List | Out-Host
    exit 0
}

if (Test-Path $LogFile) {
    Remove-Item $LogFile -Force
}

Write-Host "Starting cloudflared in user mode with http2..."
Start-Process -FilePath $cloudflaredExe `
    -ArgumentList 'tunnel', '--protocol', 'http2', '--loglevel', 'info', '--logfile', $LogFile, 'run', '--token', $token, '--url', 'http://127.0.0.1:3100' `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

Start-Sleep -Seconds 5

Write-Host ""
Write-Host "Tail of $LogFile"
Get-Content $LogFile -Tail 20 | Out-Host
