param()

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Error "Run this script in PowerShell as Administrator."
}

$service = Get-CimInstance Win32_Service -Filter "Name='cloudflared'"
if (-not $service) {
    Write-Error "cloudflared service was not found."
}

$currentPath = $service.PathName
if (-not $currentPath) {
    Write-Error "cloudflared service has an empty PathName."
}

if ($currentPath -match '\s--protocol\s+http2(\s|$)') {
    Write-Host "cloudflared service is already configured for http2."
} else {
    $updatedPath = $currentPath -replace '\s+tunnel\s+', ' tunnel --protocol http2 '
    if ($updatedPath -eq $currentPath) {
        Write-Error "Could not inject --protocol http2 into cloudflared service command."
    }

    $cmd = 'sc.exe config cloudflared binPath= "{0}"' -f $updatedPath.Replace('"', '\"')
    cmd.exe /c $cmd | Out-Host
}

Write-Host "Restarting cloudflared service..."
cmd.exe /c "sc.exe stop cloudflared" | Out-Host
Start-Sleep -Seconds 3
cmd.exe /c "sc.exe start cloudflared" | Out-Host
Start-Sleep -Seconds 3

$updatedService = Get-CimInstance Win32_Service -Filter "Name='cloudflared'"
Write-Host ""
Write-Host "Current service command:"
Write-Host $updatedService.PathName
Write-Host ""
Write-Host "Status: $($updatedService.State)"
