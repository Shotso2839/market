param(
    [int]$Port = 3100,
    [string]$TunnelUser = "nokey@localhost.run"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$envFile = Join-Path $backendDir ".env"
$outLog = Join-Path $root ".tmp-public-tunnel.out.log"
$errLog = Join-Path $root ".tmp-public-tunnel.err.log"

function Stop-PreviousTunnel {
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "ssh.exe" -and $_.CommandLine -match "localhost\.run" -and $_.CommandLine -match "127\.0\.0\.1:$Port"
    }

    foreach ($proc in $procs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning "Failed to stop old tunnel process $($proc.ProcessId): $_"
        }
    }
}

function Start-Gateway {
    $existing = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" } |
        Select-Object -First 1

    if ($existing) {
        Write-Host "Gateway already listening on port $Port"
        return
    }

    Write-Host "Starting local gateway on port $Port..."
    Start-Process -FilePath cmd.exe `
        -ArgumentList "/c", "set PORT=$Port&& node scripts/dev-gateway.js" `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $root ".tmp-gateway3100.out.log") `
        -RedirectStandardError (Join-Path $root ".tmp-gateway3100.err.log") `
        -WindowStyle Hidden | Out-Null

    Start-Sleep -Seconds 3
}

function Update-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $escaped = [Regex]::Escape($Name)
    $content = Get-Content $envFile -Raw
    $pattern = "(?m)^$escaped=.*$"
    $replacement = "$Name=$Value"

    if ($content -match $pattern) {
        $content = [Regex]::Replace($content, $pattern, $replacement)
    } else {
        $content += "`r`n$replacement`r`n"
    }

    Set-Content -Path $envFile -Value $content -Encoding UTF8
}

function Add-OriginToEnv {
    param([string]$Url)

    $content = Get-Content $envFile -Raw
    $pattern = "(?m)^ALLOWED_ORIGINS=(.*)$"
    if (-not ($content -match $pattern)) {
        return
    }

    $current = $Matches[1]
    try {
        $origins = $current | ConvertFrom-Json
    } catch {
        Write-Warning "Could not parse ALLOWED_ORIGINS; leaving it unchanged"
        return
    }

    if ($origins -notcontains $Url) {
        $origins = @($origins) + $Url
        $json = ($origins | ConvertTo-Json -Compress)
        $content = [Regex]::Replace($content, $pattern, "ALLOWED_ORIGINS=$json")
        Set-Content -Path $envFile -Value $content -Encoding UTF8
    }
}

function Restart-BackendServices {
    Write-Host "Recreating API and Telegram bot..."
    docker compose up -d --force-recreate api telegram-bot | Out-Host
}

Stop-PreviousTunnel
Start-Gateway

if (Test-Path $outLog) { Remove-Item $outLog -Force }
if (Test-Path $errLog) { Remove-Item $errLog -Force }

Write-Host "Starting public HTTPS tunnel..."
Start-Process -FilePath ssh `
    -ArgumentList "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=60", "-o", "ExitOnForwardFailure=yes", "-R", "80:127.0.0.1:$Port", $TunnelUser `
    -WorkingDirectory $root `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden | Out-Null

$publicUrl = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $outLog) {
        $log = Get-Content $outLog -Raw
        $match = [Regex]::Match($log, "https://[a-zA-Z0-9.-]+")
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
    }
}

if (-not $publicUrl) {
    Write-Error "Could not detect public HTTPS URL. Check $outLog and $errLog"
}

Update-EnvValue -Name "MINI_APP_URL" -Value $publicUrl
Add-OriginToEnv -Url $publicUrl
Restart-BackendServices

Write-Host ""
Write-Host "Public URL: $publicUrl"
Write-Host "Mini App manifest: $publicUrl/tonconnect-manifest.json"
