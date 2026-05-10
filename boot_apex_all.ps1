cd "C:\Users\The Urban Genius\Documents\Arbitrage\FINAL BUILD\Apex-Omega-v6"

@'
$ErrorActionPreference = "Stop"

$repo = Get-Location
$runtime = Join-Path $repo "runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Load-EnvFile($path) {
    if (!(Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -match "^\s*$" -or $_ -notmatch "=") { return }
        $parts = $_ -split "=", 2
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

function Start-NewTerminal($title, $command) {
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle='$title'; cd `"$repo`"; $command"
    )
}

Write-Host "=== APEX MASTER BOOT ==="

# 1. Load .env
Load-EnvFile ".\.env"

# 2. Run latency monitor first
Write-Host "=== ENDPOINT LATENCY MONITOR ==="
python ".\tools\endpoint_latency_monitor.py"

# 3. Load selected active endpoints
Load-EnvFile ".\runtime\active_endpoints.env"

if (-not $env:POLYGON_RPC_URL) {
    throw "POLYGON_RPC_URL missing after latency monitor. Check runtime\active_endpoints.env"
}

# 4. Redis
Write-Host "=== STARTING REDIS ==="
if (Get-Command redis-server -ErrorAction SilentlyContinue) {
    Start-NewTerminal "APEX Redis" "redis-server"
} else {
    Write-Host "[WARN] redis-server not found in PATH."
    Write-Host "Install Redis or run Docker: docker run -p 6379:6379 redis:7"
}

# 5. Anvil fork
Write-Host "=== STARTING ANVIL FORK ==="
if (Get-Command anvil -ErrorAction SilentlyContinue) {
    Start-NewTerminal "APEX Anvil Fork" "anvil --fork-url `"$env:POLYGON_RPC_URL`" --chain-id 137 --host 127.0.0.1 --port 8545"
} else {
    Write-Host "[WARN] anvil not found in PATH. Install Foundry first."
}

Start-Sleep -Seconds 5

# 6. API server auto-detect
Write-Host "=== STARTING API IF FOUND ==="
if (Test-Path ".\python\api_server.py") {
    Start-NewTerminal "APEX API" "cd .\python; python api_server.py"
} elseif (Test-Path ".\python\dashboard_api.py") {
    Start-NewTerminal "APEX API" "cd .\python; python dashboard_api.py"
} elseif (Test-Path ".\api") {
    Start-NewTerminal "APEX API" "cd .\api; npm install; npm run dev"
} else {
    Write-Host "[INFO] No known API entrypoint found."
}

# 7. Dashboard auto-detect
Write-Host "=== STARTING DASHBOARD IF FOUND ==="
if (Test-Path ".\dashboard\package.json") {
    Start-NewTerminal "APEX Dashboard" "cd .\dashboard; npm install; npm run dev"
} elseif (Test-Path ".\frontend\package.json") {
    Start-NewTerminal "APEX Dashboard" "cd .\frontend; npm install; npm run dev"
} elseif (Test-Path ".\ui\package.json") {
    Start-NewTerminal "APEX Dashboard" "cd .\ui; npm install; npm run dev"
} else {
    Write-Host "[INFO] No dashboard package found."
}

# 8. Telegram bot auto-detect
Write-Host "=== STARTING TELEGRAM IF FOUND ==="
if ($env:TELEGRAM_BOT_TOKEN) {
    if (Test-Path ".\python\telegram_bot.py") {
        Start-NewTerminal "APEX Telegram" "cd .\python; python telegram_bot.py"
    } elseif (Test-Path ".\telegram_bot.py") {
        Start-NewTerminal "APEX Telegram" "python telegram_bot.py"
    } else {
        Write-Host "[INFO] TELEGRAM_BOT_TOKEN exists but no telegram bot entrypoint found."
    }
} else {
    Write-Host "[INFO] TELEGRAM_BOT_TOKEN not set. Telegram skipped."
}

# 9. Main Apex bot
Write-Host "=== STARTING APEX BOT ==="
Start-NewTerminal "APEX Core Bot" "powershell -ExecutionPolicy Bypass -File .\boot_with_latency_monitor.ps1"

Write-Host ""
Write-Host "[DONE] Boot sequence launched."
Write-Host "Keep Redis + Anvil windows open."
Write-Host "Execution remains gated unless EXECUTION_ENABLED=true."
'@ | Set-Content ".\boot_apex_all.ps1" -Encoding UTF8