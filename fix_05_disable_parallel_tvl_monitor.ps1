# fix_05_disable_parallel_tvl_monitor.ps1
# Temporarily disables concurrent TVL monitor to prevent duplicate DEXScreener calls.

$ErrorActionPreference = "Stop"

$repo = Get-Location
$botFile = Join-Path $repo "python\polygon_arbitrage_bot.py"

if (!(Test-Path $botFile)) {
    throw "Missing file: $botFile"
}

$backup = "$botFile.bak_no_parallel_tvl_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $botFile $backup
Write-Host "Backup created: $backup"

$content = Get-Content $botFile -Raw

$pattern = '(?s)# Run both monitoring tasks concurrently\s+await asyncio\.gather\(\s+bot\.run_arbitrage_scan\(\),\s+bot\.monitor_pool_tvls\(\)\s+\)'

$replacement = @'
# Run primary scan loop only.
    # TVL monitor disabled temporarily to avoid duplicate DEXScreener calls
    # while provider connectivity/rate limiting is being stabilized.
    await bot.run_arbitrage_scan()
'@

if ($content -match $pattern) {
    $content = [regex]::Replace($content, $pattern, $replacement)
    Set-Content -Path $botFile -Value $content -Encoding UTF8
    Write-Host "Disabled parallel TVL monitor."
} elseif ($content -match 'await bot\.run_arbitrage_scan\(\)') {
    Write-Host "Parallel TVL monitor already appears disabled."
} else {
    Write-Host "Expected asyncio.gather block not found. Manual review may be needed."
}

Write-Host "Run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\run_apex_utf8.ps1"