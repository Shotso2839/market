param(
    [int]$Port = 3100,
    [long]$DevChatId = 0,
    [string]$TunnelUser = "nokey@localhost.run"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$envFile = Join-Path $backendDir ".env"
$GatewayPort = $Port

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

function Get-EnvValue {
    param([string]$Name)

    $line = Get-Content $envFile | Select-String ("^" + [Regex]::Escape($Name) + "=") | Select-Object -First 1
    if (-not $line) {
        return ""
    }

    return $line.ToString().Split("=", 2)[1].Trim()
}

function Get-FreeGatewayPort {
    param([int]$StartPort)

    for ($candidate = $StartPort; $candidate -lt ($StartPort + 20); $candidate++) {
        $busy = Get-NetTCPConnection -LocalPort $candidate -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq "Listen" } |
            Select-Object -First 1

        if (-not $busy) {
            return $candidate
        }
    }

    throw "Could not find a free local port starting at $StartPort"
}

function Set-AllowedOrigins {
    param([string]$Url)

    $origins = @(
        "http://localhost:3000",
        "http://localhost:$GatewayPort",
        "http://127.0.0.1:$GatewayPort",
        "https://t.me"
    )

    if ($Url) {
        $origins += $Url
    }

    $json = ($origins | Select-Object -Unique | ConvertTo-Json -Compress)
    Update-EnvValue -Name "ALLOWED_ORIGINS" -Value $json
}

function Stop-Gateway {
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^node(.exe)?$" -and $_.CommandLine -match "scripts[\\/]+dev-gateway\.js"
    }

    foreach ($proc in $procs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning "Failed to stop old gateway process $($proc.ProcessId): $_"
        }
    }
}

function Start-Gateway {
    param([string]$PublicAppUrl = "")

    Stop-Gateway
    Start-Sleep -Seconds 2

    $command = "set PORT=$GatewayPort&& set BACKEND_ORIGIN=http://127.0.0.1:8000"
    if ($PublicAppUrl) {
        $command += "&& set PUBLIC_APP_URL=$PublicAppUrl"
    }
    $command += "&& node scripts/dev-gateway.js"

    Write-Host "Starting local gateway on port $GatewayPort..."
    Start-Process -FilePath cmd.exe `
        -ArgumentList "/c", $command `
        -WorkingDirectory $root `
        -WindowStyle Hidden | Out-Null

    Start-Sleep -Seconds 3
}

function Wait-Http {
    param(
        [string]$Url,
        [int]$Attempts = 20
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 5 | Out-Null
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "Timed out waiting for $Url"
}

function Restart-BackendServices {
    Write-Host "Recreating API and Telegram bot..."
    Push-Location $backendDir
    try {
        docker compose up -d --force-recreate api telegram-bot | Out-Host
    } finally {
        Pop-Location
    }
}

function Configure-Bot {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/telegram/configure-bot" -TimeoutSec 20 | Out-Null
}

function Set-DevChatMenuButton {
    param([string]$Url)

    if ($DevChatId -le 0 -or -not $Url) {
        return
    }

    $token = Get-EnvValue -Name "TELEGRAM_BOT_TOKEN"
    if (-not $token) {
        return
    }

    $payload = @{
        chat_id = $DevChatId
        menu_button = @{
            type = "web_app"
            text = "Open app"
            web_app = @{
                url = "$Url/"
            }
        }
    } | ConvertTo-Json -Compress -Depth 6

    Invoke-RestMethod `
        -Uri "https://api.telegram.org/bot$token/setChatMenuButton" `
        -Method Post `
        -ContentType "application/json" `
        -Body $payload | Out-Null
}

function Send-DevChatLaunchMessage {
    param([string]$Url)

    if ($DevChatId -le 0 -or -not $Url) {
        return
    }

    $token = Get-EnvValue -Name "TELEGRAM_BOT_TOKEN"
    if (-not $token) {
        return
    }

    $payload = @{
        chat_id = $DevChatId
        text = "TONPRED local mode is ready. Open the fresh button below."
        reply_markup = @{
            inline_keyboard = @(
                @(
                    @{
                        text = "Open app"
                        web_app = @{
                            url = "$Url/"
                        }
                    }
                )
            )
        }
    } | ConvertTo-Json -Compress -Depth 8

    Invoke-RestMethod `
        -Uri "https://api.telegram.org/bot$token/sendMessage" `
        -Method Post `
        -ContentType "application/json" `
        -Body $payload | Out-Null
}

function Stop-PreviousTunnel {
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "ssh.exe" -and $_.CommandLine -match "localhost\.run" -and $_.CommandLine -match "127\.0\.0\.1:\d+"
    }

    foreach ($proc in $procs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning "Failed to stop old localhost.run tunnel $($proc.ProcessId): $_"
        }
    }
}

function Start-LocalhostRunTunnel {
    $runId = Get-Date -Format "yyyyMMddHHmmssfff"
    $outLog = Join-Path $root ".tmp-localhostrun.$runId.out.log"
    $errLog = Join-Path $root ".tmp-localhostrun.$runId.err.log"

    Stop-PreviousTunnel
    Start-Sleep -Seconds 2

    Write-Host "Starting HTTPS relay via localhost.run..."
    Start-Process -FilePath "ssh.exe" `
        -ArgumentList "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=60", "-o", "ExitOnForwardFailure=yes", "-R", "80:127.0.0.1:$GatewayPort", $TunnelUser `
        -WorkingDirectory $root `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden | Out-Null

    $publicUrl = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $logs = ""
        if (Test-Path $outLog) {
            $logs += (Get-Content $outLog -Raw)
        }
        if (Test-Path $errLog) {
            $logs += "`n" + (Get-Content $errLog -Raw)
        }

        $match = [Regex]::Match($logs, "https://[a-zA-Z0-9.-]+")
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
    }

    if (-not $publicUrl) {
        throw "Could not detect localhost.run URL. Check $outLog and $errLog"
    }

    return $publicUrl
}

Restart-BackendServices
Wait-Http -Url "http://127.0.0.1:8000/health"

$GatewayPort = Get-FreeGatewayPort -StartPort $Port
Start-Gateway
Wait-Http -Url "http://127.0.0.1:$GatewayPort/health"

$publicUrl = Start-LocalhostRunTunnel

Update-EnvValue -Name "MINI_APP_URL" -Value $publicUrl
Set-AllowedOrigins -Url $publicUrl

Restart-BackendServices
Wait-Http -Url "http://127.0.0.1:8000/health"

Start-Gateway -PublicAppUrl $publicUrl
Wait-Http -Url "http://127.0.0.1:$GatewayPort/health"
Wait-Http -Url "http://127.0.0.1:$GatewayPort/tonconnect-manifest.json"

Configure-Bot
Set-DevChatMenuButton -Url $publicUrl
Send-DevChatLaunchMessage -Url $publicUrl

Write-Host ""
Write-Host "Local gateway: http://127.0.0.1:$GatewayPort"
Write-Host "Public URL: $publicUrl"
Write-Host "Mini App: $publicUrl/"
Write-Host "Manifest: $publicUrl/tonconnect-manifest.json"
