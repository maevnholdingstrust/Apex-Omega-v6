# fix_02_run_utf8_bot.ps1
# One-click UTF-8 safe launcher

$ErrorActionPreference = "Stop"

$repo = Get-Location
$pythonDir = Join-Path $repo "python"

if (!(Test-Path $pythonDir)) {
    throw "Missing python folder: $pythonDir"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

chcp 65001 | Out-Null

Set-Location $pythonDir

Write-Host "=== STARTING APEX-OMEGA BOT WITH UTF-8 SAFE CONSOLE ==="
python polygon_arbitrage_bot.py