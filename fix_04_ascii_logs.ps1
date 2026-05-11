# fix_04_ascii_logs.ps1
# ASCII-safe log patch for Windows PowerShell.
# Run from repo root:
# powershell -ExecutionPolicy Bypass -File .\fix_04_ascii_logs.ps1

$ErrorActionPreference = "Stop"

$repo = Get-Location
$botFile = Join-Path $repo "python\polygon_arbitrage_bot.py"

if (!(Test-Path $botFile)) {
    throw "Missing file: $botFile"
}

$backup = "$botFile.bak_ascii_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $botFile $backup
Write-Host "Backup created: $backup"

$content = Get-Content $botFile -Raw

# Directly replace the logger strings with clean ASCII.
$content = $content.Replace('logger.info("🔍 INITIALIZING TRANSPARENT ARBITRAGE BOT")', 'logger.info("[INIT] INITIALIZING TRANSPARENT ARBITRAGE BOT")')
$content = $content.Replace('logger.info("📊 CONFIGURATION:")', 'logger.info("[CONFIG] CONFIGURATION:")')
$content = $content.Replace('logger.info(f"🎯 TARGET TOKENS: {len(self.tokens)} tokens configured")', 'logger.info(f"[TARGET] TARGET TOKENS: {len(self.tokens)} tokens configured")')
$content = $content.Replace('logger.info("🚀 STARTING ARBITRAGE SCAN LOOP")', 'logger.info("[START] STARTING ARBITRAGE SCAN LOOP")')
$content = $content.Replace('logger.info(f"🔄 SCAN #{scan_count} - Starting data intake phase")', 'logger.info(f"[SCAN] SCAN #{scan_count} - Starting data intake phase")')
$content = $content.Replace('logger.info("📥 PHASE 1: DATA INTAKE")', 'logger.info("[INTAKE] PHASE 1: DATA INTAKE")')
$content = $content.Replace('logger.info(f"   ✅ Data intake completed in {intake_time:.3f}s")', 'logger.info(f"   [OK] Data intake completed in {intake_time:.3f}s")')
$content = $content.Replace('logger.info(f"   📊 Pools discovered: {len(pools)}")', 'logger.info(f"   [POOLS] Pools discovered: {len(pools)}")')
$content = $content.Replace('logger.info(f"   💰 Total TVL scanned: ${total_tvl:,.0f} USD")', 'logger.info(f"   [TVL] Total TVL scanned: ${total_tvl:,.0f} USD")')
$content = $content.Replace('logger.info("🔍 PHASE 2: OPPORTUNITY DISCOVERY")', 'logger.info("[DISCOVERY] PHASE 2: OPPORTUNITY DISCOVERY")')
$content = $content.Replace('logger.info(f"   ✅ Opportunity discovery completed in {discovery_time:.3f}s")', 'logger.info(f"   [OK] Opportunity discovery completed in {discovery_time:.3f}s")')
$content = $content.Replace('logger.info(f"   🎯 Opportunities found: {len(opportunities)}")', 'logger.info(f"   [TARGET] Opportunities found: {len(opportunities)}")')
$content = $content.Replace('logger.info(f"   📈 OPPORTUNITY #{i}:")', 'logger.info(f"   [OPPORTUNITY] OPPORTUNITY #{i}:")')
$content = $content.Replace('logger.info("🛣️ PHASE 3: ROUTE OPTIMIZATION")', 'logger.info("[ROUTE] PHASE 3: ROUTE OPTIMIZATION")')
$content = $content.Replace('logger.info(f"   ✅ Route optimization completed in {route_time:.3f}s")', 'logger.info(f"   [OK] Route optimization completed in {route_time:.3f}s")')
$content = $content.Replace('logger.info(f"   🔀 Optimized routes: {len(optimized_opps)}")', 'logger.info(f"   [ROUTES] Optimized routes: {len(optimized_opps)}")')
$content = $content.Replace('logger.info("⚡ PHASE 4: EXECUTION PHASE")', 'logger.info("[EXEC] PHASE 4: EXECUTION PHASE")')
$content = $content.Replace('logger.info(f"   🎯 EXECUTING OPPORTUNITY:")', 'logger.info(f"   [EXEC] EXECUTING OPPORTUNITY:")')
$content = $content.Replace('logger.info(f"      ✅ EXECUTION SUCCESSFUL in {exec_time:.3f}s")', 'logger.info(f"      [OK] EXECUTION SUCCESSFUL in {exec_time:.3f}s")')
$content = $content.Replace('logger.info(f"      📋 Transaction Hash: {result.tx_hash}")', 'logger.info(f"      [TX] Transaction Hash: {result.tx_hash}")')
$content = $content.Replace('logger.info(f"      📊 Actual Slippage: {result.slippage.difference:.6f}")', 'logger.info(f"      [SLIPPAGE] Actual Slippage: {result.slippage.difference:.6f}")')
$content = $content.Replace('logger.warning(f"      ❌ EXECUTION FAILED in {exec_time:.3f}s")', 'logger.warning(f"      [FAIL] EXECUTION FAILED in {exec_time:.3f}s")')
$content = $content.Replace('logger.info(f"   📊 Execution Summary: {executed_count}/{len(optimized_opps)} successful")', 'logger.info(f"   [SUMMARY] Execution Summary: {executed_count}/{len(optimized_opps)} successful")')
$content = $content.Replace('logger.info("⏭️ PHASE 3-4: SKIPPED (no opportunities found)")', 'logger.info("[SKIP] PHASE 3-4: SKIPPED (no opportunities found)")')
$content = $content.Replace('logger.info(f"🔚 SCAN #{scan_count} COMPLETED in {scan_time:.3f}s")', 'logger.info(f"[DONE] SCAN #{scan_count} COMPLETED in {scan_time:.3f}s")')
$content = $content.Replace('logger.info(f"   📈 Performance: {len(pools)} pools scanned, {len(opportunities)} opportunities found")', 'logger.info(f"   [PERF] Performance: {len(pools)} pools scanned, {len(opportunities)} opportunities found")')
$content = $content.Replace('logger.error(f"❌ CRITICAL ERROR in scan #{scan_count}: {e}")', 'logger.error(f"[CRITICAL] CRITICAL ERROR in scan #{scan_count}: {e}")')
$content = $content.Replace('logger.error(f"   🔄 Continuing with next scan...")', 'logger.error(f"   [CONTINUE] Continuing with next scan...")')
$content = $content.Replace('logger.info("⏳ Waiting 10 seconds before next scan...")', 'logger.info("[WAIT] Waiting 10 seconds before next scan...")')
$content = $content.Replace('logger.info("📊 STARTING TVL MONITORING")', 'logger.info("[TVL] STARTING TVL MONITORING")')
$content = $content.Replace('logger.info("💰 TVL CHECK: Scanning pool liquidity")', 'logger.info("[TVL] TVL CHECK: Scanning pool liquidity")')
$content = $content.Replace('logger.info(f"💵 TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")', 'logger.info(f"[TVL] TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")')
$content = $content.Replace('logger.info("🏆 TOP 5 POOLS BY TVL:")', 'logger.info("[TOP] TOP 5 POOLS BY TVL:")')
$content = $content.Replace('logger.error(f"❌ TVL monitoring error: {e}")', 'logger.error(f"[TVL-ERROR] TVL monitoring error: {e}")')
$content = $content.Replace('logger.info("🎯 APEX-OMEGA-V6 POLYGON ARBITRAGE BOT STARTING")', 'logger.info("[START] APEX-OMEGA-V6 POLYGON ARBITRAGE BOT STARTING")')

# Replace bullet glyphs in normal string literals.
$content = $content.Replace('•', '-')

Set-Content -Path $botFile -Value $content -Encoding UTF8

Write-Host "ASCII logging patch complete."
Write-Host "Run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\run_apex_utf8.ps1"