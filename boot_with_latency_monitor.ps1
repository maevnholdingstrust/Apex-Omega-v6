# boot_with_latency_monitor.ps1
# Runs endpoint latency monitor, loads fastest endpoint selections, then starts bot.

$ErrorActionPreference = "Stop"

$repo = Get-Location
$pythonDir = Join-Path $repo "python"
$activeEnv = Join-Path $repo "runtime\active_endpoints.env"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null

Write-Host "=== APEX BOOT: ENDPOINT LATENCY MONITOR ==="
python ".\tools\endpoint_latency_monitor.py"

if (Test-Path $activeEnv) {
    Write-Host "=== LOADING ACTIVE ENDPOINT SELECTIONS ==="
    Get-Content $activeEnv | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -match "^\s*$") { return }
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
            Write-Host ("  " + $parts[0] + "=<selected>")
        }
    }
}

if (-not $env:EXECUTOR_PRIVATE_KEY -and $env:PRIVATE_KEY) { $env:EXECUTOR_PRIVATE_KEY = $env:PRIVATE_KEY }
if (-not $env:APEX_PRIVATE_KEY -and $env:EXECUTOR_PRIVATE_KEY) { $env:APEX_PRIVATE_KEY = $env:EXECUTOR_PRIVATE_KEY }
if (-not $env:AAVE_V3_POOL_ADDRESS -and $env:AAVE_POOL_ADDRESS) { $env:AAVE_V3_POOL_ADDRESS = $env:AAVE_POOL_ADDRESS }
if (-not $env:BALANCER_VAULT_ADDRESS -and $env:BALANCER_VAULT) { $env:BALANCER_VAULT_ADDRESS = $env:BALANCER_VAULT }
if (-not $env:AAVE_V3_POOL_ADDRESS) { $env:AAVE_V3_POOL_ADDRESS = "0x794a61358D6845594F94dc1DB02A252b5b4814aD" }
if (-not $env:BALANCER_VAULT_ADDRESS) { $env:BALANCER_VAULT_ADDRESS = "0xBA12222222228d8Ba445958a75a0704d566BF2C8" }
if (-not $env:C1_INSTITUTIONAL_EXECUTOR_ADDRESS) { $env:C1_INSTITUTIONAL_EXECUTOR_ADDRESS = "0x05c43ef06057F1fb8FCA7E76dC2029a366deC225" }
if (-not $env:C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS) { $env:C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS = "0x8B04b0db6e803Bc29C3327885351D4297ABad9BE" }
if (-not $env:LIQUIDATION_EXECUTOR_ADDRESS) { $env:LIQUIDATION_EXECUTOR_ADDRESS = "0xF9a28f389Ad8c33F9da68c736BEAf1F2A3795a56" }
if (-not $env:LIVE_TRADING_ENABLED) {
    if ($env:LIVE_EXECUTION -eq "true") { $env:LIVE_TRADING_ENABLED = "true" } else { $env:LIVE_TRADING_ENABLED = "false" }
}
if ($env:LIVE_EXECUTION -eq "true" -or $env:LIVE_TRADING_ENABLED -eq "true") {
    $env:LIVE_EXECUTION = "true"
    $env:LIVE_TRADING_ENABLED = "true"
    $env:DRY_RUN = "false"
    $env:APEX_SEND_TX = "1"
} else {
    if (-not $env:DRY_RUN) { $env:DRY_RUN = "true" }
    if (-not $env:APEX_SEND_TX) { $env:APEX_SEND_TX = "0" }
}

Write-Host "=== STARTING APEX BOT ==="
Set-Location $pythonDir
python polygon_arbitrage_bot.py

