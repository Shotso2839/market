param(
    [string]$PublicAppUrl = "https://app.tonpred.com",
    [int]$Port = 3100
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$envFile = Join-Path $backendDir ".env"
$frontendManifest = Join-Path $root "frontend\tonconnect-manifest.json"
$gatewayOutLog = Join-Path $root ".tmp-gateway3100.out.log"
$gatewayErrLog = Join-Path $root ".tmp-gateway3100.err.log"
$userCloudflaredScript = Join-Path $PSScriptRoot "run-cloudflared-http2.ps1"

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

function Update-FrontendManifest {
    param([string]$BaseUrl)

    $manifest = @{
        url = $BaseUrl
        name = "TON Prediction"
        iconUrl = "$BaseUrl/icon-180.png"
        termsOfUseUrl = "$BaseUrl/terms.html"
        privacyPolicyUrl = "$BaseUrl/privacy.html"
    } | ConvertTo-Json

    Set-Content -Path $frontendManifest -Value $manifest -Encoding UTF8
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
    Stop-Gateway
    Start-Sleep -Seconds 1

    foreach ($logFile in @($gatewayOutLog, $gatewayErrLog)) {
        if (-not (Test-Path $logFile)) {
            continue
        }

        try {
            Remove-Item $logFile -Force -ErrorAction Stop
        } catch {
            # If Windows still keeps the handle for a moment, truncate instead of failing startup.
            Set-Content -Path $logFile -Value "" -Encoding UTF8
        }
    }

    Write-Host "Starting local gateway on port $Port..."
    Start-Process -FilePath cmd.exe `
        -ArgumentList "/c", "set PORT=$Port&& set BACKEND_ORIGIN=http://127.0.0.1:8000&& set PUBLIC_APP_URL=$PublicAppUrl&& node scripts/dev-gateway.js" `
        -WorkingDirectory $root `
        -RedirectStandardOutput $gatewayOutLog `
        -RedirectStandardError $gatewayErrLog `
        -WindowStyle Hidden | Out-Null

    Start-Sleep -Seconds 3
}

function Ensure-CloudflaredService {
    $service = Get-Service cloudflared -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Warning "cloudflared service was not found. Install it once in Cloudflare Zero Trust."
        return
    }

    if ($service.Status -ne "Running") {
        Write-Host "Starting cloudflared service..."
        Start-Service cloudflared
        Start-Sleep -Seconds 2
    }
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

function Configure-Bot {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/telegram/configure-bot" -TimeoutSec 20 | Out-Null
}

function Ensure-UserCloudflared {
    if (-not (Test-Path $userCloudflaredScript)) {
        Write-Warning "User-mode cloudflared helper script was not found: $userCloudflaredScript"
        return
    }

    Write-Host "Ensuring user-mode cloudflared http2 connector..."
    powershell -ExecutionPolicy Bypass -File $userCloudflaredScript | Out-Host
}

Update-EnvValue -Name "MINI_APP_URL" -Value $PublicAppUrl
Add-OriginToEnv -Url $PublicAppUrl
Update-FrontendManifest -BaseUrl $PublicAppUrl

Ensure-CloudflaredService
Restart-BackendServices
Wait-Http -Url "http://127.0.0.1:8000/health"

Start-Gateway
Wait-Http -Url "http://127.0.0.1:$Port/health"
Ensure-UserCloudflared

Configure-Bot

Write-Host ""
Write-Host "Public URL: $PublicAppUrl"
Write-Host "Gateway health: http://127.0.0.1:$Port/health"
Write-Host "Mini App manifest: $PublicAppUrl/tonconnect-manifest.json"
