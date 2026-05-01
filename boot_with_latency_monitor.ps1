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

Write-Host "=== STARTING APEX BOT ==="
Set-Location $pythonDir
python polygon_arbitrage_bot.py

