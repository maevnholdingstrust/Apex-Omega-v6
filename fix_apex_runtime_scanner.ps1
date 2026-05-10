# fix_apex_runtime_scanner.ps1
# Run from repo root:
# powershell -ExecutionPolicy Bypass -File .\fix_apex_runtime_scanner.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== APEX-OMEGA RUNTIME + SCANNER PATCH ==="

$repo = Get-Location
$botFile = Join-Path $repo "python\polygon_arbitrage_bot.py"
$arbFile = Join-Path $repo "python\apex_omega_core\core\polygon_arbitrage.py"

if (!(Test-Path $botFile)) { throw "Missing file: $botFile" }
if (!(Test-Path $arbFile)) { throw "Missing file: $arbFile" }

$timestamp = Get-Date -Format yyyyMMdd_HHmmss

Copy-Item $botFile "$botFile.bak_apex_$timestamp"
Copy-Item $arbFile "$arbFile.bak_apex_$timestamp"

Write-Host "Backups created:"
Write-Host "  $botFile.bak_apex_$timestamp"
Write-Host "  $arbFile.bak_apex_$timestamp"

# --------------------------------------------------------------------
# PATCH 1: polygon_arbitrage_bot.py UTF-8 runtime + UTF-8 FileHandler
# --------------------------------------------------------------------

$bot = Get-Content $botFile -Raw

$utf8Patch = @'
# --- APEX PATCH: Windows UTF-8 stdout/stderr safety ---
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# --- END APEX PATCH ---

'@

if ($bot -notmatch "APEX PATCH: Windows UTF-8 stdout/stderr safety") {
    $bot = $bot -replace 'import asyncio\r?\n', "import asyncio`r`n$utf8Patch"
    Write-Host "Patched UTF-8 stdout/stderr safety."
} else {
    Write-Host "UTF-8 stdout/stderr patch already present."
}

$oldFileHandler = "logging.FileHandler('apex_omega_transparent.log')"
$newFileHandler = "logging.FileHandler('apex_omega_transparent.log', encoding='utf-8')"

if ($bot.Contains($oldFileHandler)) {
    $bot = $bot.Replace($oldFileHandler, $newFileHandler)
    Write-Host "Patched FileHandler encoding=utf-8."
} elseif ($bot.Contains($newFileHandler)) {
    Write-Host "FileHandler UTF-8 patch already present."
} else {
    Write-Host "WARNING: Expected FileHandler line not found. Review logging config manually."
}

Set-Content -Path $botFile -Value $bot -Encoding UTF8

# --------------------------------------------------------------------
# PATCH 2: polygon_arbitrage.py scanner None guards + terminal state
# --------------------------------------------------------------------

$arb = Get-Content $arbFile -Raw

# 2A: Add scanner diagnostics fields in __init__
$oldInitBlock = @'
        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._last_registry_refresh: float = 0.0
'@

$newInitBlock = @'
        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.scanner_errors: int = 0
        self.last_scan_terminal_state: str = "NOT_STARTED"
        self._last_registry_refresh: float = 0.0
'@

if ($arb.Contains($oldInitBlock) -and $arb -notmatch "self.scanner_errors") {
    $arb = $arb.Replace($oldInitBlock, $newInitBlock)
    Write-Host "Patched scanner diagnostics fields."
} else {
    Write-Host "Scanner diagnostics fields already present or anchor missing."
}

# 2B: Replace scan_all_dexes with safer version
$oldScanAll = @'
    async def scan_all_dexes(self, tokens: List[Any]) -> List[Pool]:
        """Scan all DEXes for pools containing specified tokens."""
        if not self.token_metadata:
            await self.refresh_market_registry()

        normalized_tokens = self._normalize_tokens(tokens)
        all_pools = []
        for dex_name, factory_address in self._all_dexes.items():
            if not dex_name or not factory_address:
                continue
            try:
                pools = await self._scan_dex_pools(dex_name, factory_address, normalized_tokens)
                all_pools.extend(pools)
                logger.info(f"Scanned {len(pools)} pools on {dex_name}")
            except Exception as e:
                logger.error(f"Error scanning {dex_name}: {e}")
        return all_pools
'@

$newScanAll = @'
    async def scan_all_dexes(self, tokens: List[Any]) -> List[Pool]:
        """Scan all DEXes for pools containing specified tokens.

        Production invariant:
        - never return None
        - never allow one DEX failure to collapse the entire scan
        - distinguish data-intake failure from true no-opportunity state
        """
        if not self.token_metadata:
            await self.refresh_market_registry()

        self.scanner_errors = 0
        self.last_scan_terminal_state = "SCANNING"

        normalized_tokens = self._normalize_tokens(tokens)
        all_pools: List[Pool] = []

        if not normalized_tokens:
            self.last_scan_terminal_state = "NO_VALID_TOKENS"
            logger.warning("No valid normalized tokens available for DEX scan")
            return []

        for dex_name, factory_address in self._all_dexes.items():
            if not dex_name or not factory_address:
                continue

            try:
                pools = await self._scan_dex_pools(dex_name, factory_address, normalized_tokens)

                if pools is None:
                    self.scanner_errors += 1
                    logger.warning("DEX %s returned None; treating as empty pool list", dex_name)
                    pools = []

                if not isinstance(pools, list):
                    try:
                        pools = list(pools)
                    except Exception as exc:
                        self.scanner_errors += 1
                        logger.exception("DEX %s returned non-list/non-iterable pool result: %s", dex_name, exc)
                        pools = []

                all_pools.extend(pools)
                logger.info("Scanned %d pools on %s", len(pools), dex_name)

            except Exception as e:
                self.scanner_errors += 1
                logger.exception("Error scanning %s: %s", dex_name, e)

        if self.scanner_errors > 0 and len(all_pools) == 0:
            self.last_scan_terminal_state = "REJECTED_BY_DATA_INTAKE_FAILURE"
        elif len(all_pools) == 0:
            self.last_scan_terminal_state = "NO_POOLS_DISCOVERED"
        else:
            self.last_scan_terminal_state = "POOLS_DISCOVERED"

        logger.info(
            "DEX scan terminal_state=%s scanner_errors=%d pools=%d",
            self.last_scan_terminal_state,
            self.scanner_errors,
            len(all_pools),
        )

        return all_pools
'@

if ($arb.Contains($oldScanAll)) {
    $arb = $arb.Replace($oldScanAll, $newScanAll)
    Write-Host "Patched scan_all_dexes None-safe behavior."
} elseif ($arb -match "last_scan_terminal_state") {
    Write-Host "scan_all_dexes patch appears already present."
} else {
    Write-Host "WARNING: scan_all_dexes anchor not found. Manual review needed."
}

# 2C: Patch _scan_dex_pools pair fetch guard
$oldPairFetch = @'
            pairs = await self._fetch_live_pairs_for_token(addr)
            for pair in pairs:
'@

$newPairFetch = @'
            pairs = await self._fetch_live_pairs_for_token(addr)
            if pairs is None:
                logger.warning("No pair data returned for token %s on %s; skipping token", addr, dex_name)
                continue
            if not isinstance(pairs, list):
                try:
                    pairs = list(pairs)
                except Exception as exc:
                    logger.exception("Invalid pair data for token %s on %s: %s", addr, dex_name, exc)
                    continue

            for pair in pairs:
'@

if ($arb.Contains($oldPairFetch)) {
    $arb = $arb.Replace($oldPairFetch, $newPairFetch)
    Write-Host "Patched _scan_dex_pools pair iteration guard."
} else {
    Write-Host "Pair iteration guard already present or anchor missing."
}

# 2D: Replace _fetch_live_pairs_for_token with safer version
$oldFetchPairs = @'
    async def _fetch_live_pairs_for_token(self, address: str) -> List[Dict[str, Any]]:
        """Fetch and cache DEX pair metadata for a token via DEXScreener."""
        if address in self._token_pool_cache:
            return self._token_pool_cache[address]

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            data = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")

        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        normalized_pairs = [p for p in pairs if isinstance(p, dict)]
        self._token_pool_cache[address] = normalized_pairs
        return normalized_pairs
'@

$newFetchPairs = @'
    async def _fetch_live_pairs_for_token(self, address: str) -> List[Dict[str, Any]]:
        """Fetch and cache DEX pair metadata for a token via DEXScreener.

        Production invariant:
        - always return list
        - never return None
        - tolerate malformed upstream payloads
        """
        if address in self._token_pool_cache:
            cached = self._token_pool_cache.get(address)
            return cached if isinstance(cached, list) else []

        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        except Exception as exc:
            logger.exception("DEXScreener pair fetch failed for token %s: %s", address, exc)
            self._token_pool_cache[address] = []
            return []

        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        if pairs is None:
            pairs = []

        if not isinstance(pairs, list):
            logger.warning(
                "DEXScreener returned malformed pairs for token %s: %s",
                address,
                type(pairs).__name__,
            )
            pairs = []

        normalized_pairs = [p for p in pairs if isinstance(p, dict)]
        self._token_pool_cache[address] = normalized_pairs
        return normalized_pairs
'@

if ($arb.Contains($oldFetchPairs)) {
    $arb = $arb.Replace($oldFetchPairs, $newFetchPairs)
    Write-Host "Patched _fetch_live_pairs_for_token to always return list."
} elseif ($arb -match "Production invariant:\r?\n        - always return list") {
    Write-Host "_fetch_live_pairs_for_token patch already present."
} else {
    Write-Host "WARNING: _fetch_live_pairs_for_token anchor not found. Manual review needed."
}

Set-Content -Path $arbFile -Value $arb -Encoding UTF8

# --------------------------------------------------------------------
# PATCH 3: Create safe launcher
# --------------------------------------------------------------------

$launcherFile = Join-Path $repo "run_apex_utf8.ps1"

$launcher = @'
# run_apex_utf8.ps1
# UTF-8 safe Apex-Omega launcher

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
'@

Set-Content -Path $launcherFile -Value $launcher -Encoding UTF8
Write-Host "Created UTF-8 launcher: $launcherFile"

Write-Host ""
Write-Host "=== PATCH COMPLETE ==="
Write-Host "Run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\run_apex_utf8.ps1"