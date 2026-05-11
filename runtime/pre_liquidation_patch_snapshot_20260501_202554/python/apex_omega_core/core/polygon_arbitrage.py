import asyncio
import logging
import time
import os
import inspect
import math
from typing import Any, Dict, List, Optional
from pathlib import Path

import aiohttp
from .pool_math_registry import classify_pool_kwargs
from .onchain_v2_discovery import discover_v2_pools_onchain, OnchainV2Pool
from web3 import Web3
from .domain_types import Pool, ArbitrageOpportunity, FlashLoanConfig

logger = logging.getLogger(__name__)

# Load .env from apex_omega_core directory
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        logger.warning("python-dotenv not installed; skipping .env autoload for %s", env_path)

POLYGON_CANONICAL_TOKEN_METADATA = {
    # stables
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": {"symbol": "USDC.e", "decimals": 6, "price_usd": 1.0},
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": {"symbol": "USDC", "decimals": 6, "price_usd": 1.0},
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": {"symbol": "USDT", "decimals": 6, "price_usd": 1.0},
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": {"symbol": "DAI", "decimals": 18, "price_usd": 1.0},
    "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89": {"symbol": "FRAX", "decimals": 18, "price_usd": 1.0},

    # majors: prices are fallbacks only; update via live feed later
    "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": {"symbol": "WETH", "decimals": 18, "price_usd": 3000.0},
    "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": {"symbol": "WBTC", "decimals": 8, "price_usd": 65000.0},
    "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": {"symbol": "WPOL", "decimals": 18, "price_usd": 0.70},
    "0x0000000000000000000000000000000000001010": {"symbol": "POL", "decimals": 18, "price_usd": 0.70},
    "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39": {"symbol": "LINK", "decimals": 18, "price_usd": 15.0},
    "0x172370d5cd63279efa6d502dab29171933a610af": {"symbol": "CRV", "decimals": 18, "price_usd": 0.30},
    "0x831753dd7087cac61ab5644b308642cc1c33dc13": {"symbol": "QUICK", "decimals": 18, "price_usd": 0.04},
}

class PolygonDEXMonitor:
    """Monitor prices across all major Polygon DEXes"""

    MIN_TOKEN_TVL_USD = 50000.0
    MIN_DISCOVERY_ATTEMPTS = 5

    ERC20_SYMBOL_ABI = [
        {
            "constant": True,
            "inputs": [],
            "name": "symbol",
            "outputs": [{"name": "", "type": "string"}],
            "payable": False,
            "stateMutability": "view",
            "type": "function",
        }
    ]
    ERC20_SYMBOL_BYTES32_ABI = [
        {
            "constant": True,
            "inputs": [],
            "name": "symbol",
            "outputs": [{"name": "", "type": "bytes32"}],
            "payable": False,
            "stateMutability": "view",
            "type": "function",
        }
    ]

    def __init__(self, web3_provider: str = "https://polygon-rpc.com/"):
        self.w3 = Web3(Web3.HTTPProvider(web3_provider))
        # V2 constant-product DEX factories â€” safe to use with the V2 AMM formula.
        self.dexes: Dict[str, str] = {
            "quickswap": "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            "sushiswap": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            "apeswap": "0xCf083Be4164828f00cAE704EC15a36D711491284",
            "dfyn": "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B",
            "jetswap": "0x668ad0ed2622b0ac445205f25ee12a7d618cfb52",
        }
        # Concentrated-liquidity (V3) DEX factories â€” NOT compatible with the V2
        # constant-product formula.  Pools from these venues are tagged
        # pool_type="v3" and excluded from V2 AMM optimisation paths.
        self.v3_dexes: Dict[str, str] = {
            "uniswap": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        }
        # Combined map used for registry scanning; individual methods check
        # self.v3_dexes to set the correct pool_type.
        # Keys are disjoint: "uniswap" appears only in v3_dexes; all other
        # names appear only in dexes.  No key overwrite occurs on merge.
        self._all_dexes: Dict[str, str] = {**self.dexes, **self.v3_dexes}
        self.token_metadata: Dict[str, Dict[str, str]] = {}
        self._seed_canonical_token_metadata()
        self.pools: Dict[str, List[Pool]] = {}
        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.discovery_source: str = os.getenv("DISCOVERY_SOURCE", "onchain_v2").lower()
        self.use_dexscreener: bool = os.getenv("USE_DEXSCREENER", "false").lower() == "true"
        self.onchain_discovery_max_tokens: int = int(os.getenv("ONCHAIN_DISCOVERY_MAX_TOKENS", "80"))
        self.onchain_discovery_max_pairs: int = int(os.getenv("ONCHAIN_DISCOVERY_MAX_PAIRS", "2500"))
        self.onchain_discovery_concurrency: int = int(os.getenv("ONCHAIN_DISCOVERY_CONCURRENCY", "32"))
        self.use_dexscreener: bool = os.getenv("USE_DEXSCREENER", "false").lower() == "true"
        self.dexscreener_max_tokens_per_scan: int = int(os.getenv("DEXSCREENER_MAX_TOKENS_PER_SCAN", "25"))
        self.scanner_errors: int = 0
        self.last_scan_terminal_state: str = "NOT_STARTED"
        self._last_registry_refresh: float = 0.0
        # TTL is configurable via POLYGON_REGISTRY_TTL_SECONDS env var.
        # Default: 1 800 s (30 min).  Set to a smaller value for faster
        # discovery cycles in test or low-latency environments.
        self._registry_ttl_seconds: int = int(
            os.getenv("POLYGON_REGISTRY_TTL_SECONDS", "1800")
        )


    def _seed_canonical_token_metadata(self) -> None:
        """Seed known Polygon token metadata so TVL has sane USD anchors."""
        current = getattr(self, "token_metadata", {}) or {}
        for addr, meta in POLYGON_CANONICAL_TOKEN_METADATA.items():
            existing = current.get(addr.lower()) or {}
            merged = dict(meta)
            if isinstance(existing, dict):
                merged.update({k: v for k, v in existing.items() if v not in (None, "", 0, 0.0)})
                # Keep stable anchors fixed at 1.0 if existing bad price is missing/zero.
                if merged.get("symbol", "").upper() in {"USDC", "USDC.E", "USDT", "DAI", "FRAX"}:
                    merged["price_usd"] = 1.0
            current[addr.lower()] = merged
        self.token_metadata = current

    async def refresh_market_registry(self, max_tokens: int = 300, force: bool = False) -> None:
        """Refresh token and DEX coverage from external sources with caching."""
        self._seed_canonical_token_metadata()  # apex seed before registry merge
        now = time.time()
        if not force and self.token_metadata and now - self._last_registry_refresh < self._registry_ttl_seconds:
            return

        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Live endpoints from .env file - fail fast if not available
            oneinch_url = os.getenv("ONEINCH_API", "https://api.1inch.dev/swap/v5.2/137/quote")
            oneinch_key = os.getenv("ONEINCH_API_KEY", "")
            gecko_url = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
            llama_url = "https://api.llama.fi/protocols"
            
            # Add 1inch API key to header if available
            headers = {}
            if oneinch_key:
                headers["Authorization"] = f"Bearer {oneinch_key}"
            
            # Fetch from real sources
            oneinch_task = self._fetch_json(session, oneinch_url, headers)
            gecko_task = self._fetch_json(session, gecko_url, {})
            llama_task = self._fetch_json(session, llama_url, {})
            
            oneinch_data, gecko_data, llama_protocols_data = await asyncio.gather(
                oneinch_task,
                gecko_task,
                llama_task,
            )

        tokens = self._merge_token_sources(oneinch_data, gecko_data)
        await self._fill_missing_symbols_from_chain(tokens)
        await self._attempt_five_way_discovery(tokens)
        self.token_metadata = self._filter_and_limit_tokens(tokens, max_tokens=max_tokens)

        discovered_dexes = self._extract_polygon_dexes(llama_protocols_data)
        self.dexes.update(discovered_dexes)
        self._last_registry_refresh = now

        logger.info(
            "Polygon registry refreshed: %d tokens retained, %d DEX factories",
            len(self.token_metadata),
            len(self.dexes),
        )

    def get_tokens(self) -> List[Dict[str, str]]:
        """Return validated tokens containing both address and symbol."""
        return [
            {"address": token["address"], "symbol": token["symbol"]}
            for token in self.token_metadata.values()
            if token.get("address") and token.get("symbol")
        ]

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str, headers: Dict[str, str] = None) -> Any:
        """Fetch JSON with robust error handling, redirect following, and empty structure fallback."""
        try:
            fetch_headers = headers or {}
            # Allow redirects (e.g., 301 for 1inch API); keep TLS verification enabled.
            async with session.get(url, headers=fetch_headers, allow_redirects=True) as response:
                if response.status != 200:
                    logger.warning("Fetch failed (%s): status=%s", url, response.status)
                    return {}
                text = await response.text()
                if not text or text.strip() == "":
                    logger.warning("Fetch returned empty response (%s)", url)
                    return {}
                return await response.json(content_type=None)
        except Exception as e:
            logger.warning("Fetch failed (%s): %s", url, e)
            return {}

    def _merge_token_sources(self, oneinch_data: Any, gecko_data: Any) -> Dict[str, Dict[str, str]]:
        """Merge token metadata from multiple sources by address, filtering for Polygon chain."""
        merged: Dict[str, Dict[str, str]] = {}

        oneinch_tokens = oneinch_data.get("tokens", {}) if isinstance(oneinch_data, dict) else {}
        for address, info in oneinch_tokens.items():
            normalized = self._normalize_address(address)
            symbol = (info or {}).get("symbol")
            if normalized:
                merged[normalized] = {
                    "address": normalized,
                    "symbol": (symbol or "").strip().upper(),
                    "tvl_usd": 0.0,
                    "discovery_attempts": 1,
                }

        if isinstance(gecko_data, list):
            for coin in gecko_data:
                if not isinstance(coin, dict):
                    continue
                platforms = coin.get("platforms", {})
                address = platforms.get("polygon-pos") if isinstance(platforms, dict) else None
                normalized = self._normalize_address(address)
                if not normalized:
                    continue
                symbol = (coin.get("symbol") or "").strip().upper()
                existing = merged.get(
                    normalized,
                    {"address": normalized, "symbol": "", "tvl_usd": 0.0, "tvl_verified": False, "discovery_attempts": 0},
                )
                if symbol and not existing.get("symbol"):
                    existing["symbol"] = symbol
                existing["discovery_attempts"] = int(existing.get("discovery_attempts", 0)) + 1
                merged[normalized] = existing

        return merged

    async def _fill_missing_symbols_from_chain(self, tokens: Dict[str, Dict[str, str]]) -> None:
        """Use direct ERC-20 calls for symbols that are still missing."""
        missing = [address for address, t in tokens.items() if not t.get("symbol")]
        if not missing:
            return

        batch = missing[:500]
        tasks = [self._get_symbol_from_contract(address) for address in batch]
        symbols = await asyncio.gather(*tasks)

        for address, symbol in zip(batch, symbols):
            tokens[address]["discovery_attempts"] = int(tokens[address].get("discovery_attempts", 0)) + 1
            if symbol:
                tokens[address]["symbol"] = symbol

    async def _attempt_five_way_discovery(self, tokens: Dict[str, Dict[str, Any]]) -> None:
        """For symbol-missing tokens, keep depth by TVL and try at least five discovery methods before exclusion."""
        unresolved = [address for address, token in tokens.items() if not (token.get("symbol") or "").strip()]
        if not unresolved:
            return

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tvl_tasks = [self._estimate_token_tvl_usd(session, address) for address in unresolved]
            tvls = await asyncio.gather(*tvl_tasks)

            for address, tvl in zip(unresolved, tvls):
                tokens[address]["tvl_usd"] = float(tvl or 0.0)

            high_tvl_addresses = [
                address for address in unresolved if float(tokens[address].get("tvl_usd", 0.0)) >= self.MIN_TOKEN_TVL_USD
            ]
            if not high_tvl_addresses:
                return

            symbol_tasks = [self._discover_symbol_five_ways(session, address) for address in high_tvl_addresses]
            results = await asyncio.gather(*symbol_tasks)

            for address, (symbol, attempts) in zip(high_tvl_addresses, results):
                tokens[address]["discovery_attempts"] = max(
                    int(tokens[address].get("discovery_attempts", 0)) + attempts,
                    self.MIN_DISCOVERY_ATTEMPTS,
                )
                if symbol:
                    tokens[address]["symbol"] = symbol
                elif not tokens[address].get("symbol"):
                    # Preserve high-liquidity market depth using deterministic address-bound placeholder.
                    tokens[address]["symbol"] = f"UNK_{address[-6:]}"

    async def _discover_symbol_five_ways(self, session: aiohttp.ClientSession, address: str) -> tuple[str, int]:
        """Try at least five independent methods to resolve a token symbol."""
        methods = [
            self._get_symbol_from_contract,
            self._get_symbol_from_contract_bytes32,
            lambda addr: self._get_symbol_from_dexscreener(session, addr),
            lambda addr: self._get_symbol_from_geckoterminal(session, addr),
            lambda addr: self._get_symbol_from_coingecko_contract(session, addr),
        ]
        attempts = 0
        for method in methods:
            attempts += 1
            try:
                symbol = await method(address)
                if symbol and symbol not in {"UNKNOWN", "N/A", "-", "?"}:
                    return symbol, attempts
            except Exception:
                continue
        return "", attempts

    async def _get_symbol_from_contract(self, address: str) -> str:
        """Read token symbol directly from ERC-20 contract without blocking event loop."""
        loop = asyncio.get_running_loop()

        def _call_symbol() -> str:
            try:
                contract = self.w3.eth.contract(address=self.w3.to_checksum_address(address), abi=self.ERC20_SYMBOL_ABI)
                raw = contract.functions.symbol().call()
                if isinstance(raw, str):
                    return raw.strip().upper()
                return ""
            except Exception:
                return ""

        return await loop.run_in_executor(None, _call_symbol)

    async def _get_symbol_from_contract_bytes32(self, address: str) -> str:
        """Fallback for non-standard ERC-20 contracts that return bytes32 symbol."""
        loop = asyncio.get_running_loop()

        def _call_symbol() -> str:
            try:
                contract = self.w3.eth.contract(
                    address=self.w3.to_checksum_address(address),
                    abi=self.ERC20_SYMBOL_BYTES32_ABI,
                )
                raw = contract.functions.symbol().call()
                if isinstance(raw, (bytes, bytearray)):
                    return raw.decode("utf-8", errors="ignore").replace("\x00", "").strip().upper()
                return ""
            except Exception:
                return ""

        return await loop.run_in_executor(None, _call_symbol)

    async def _get_symbol_from_dexscreener(self, session: aiohttp.ClientSession, address: str) -> str:
        """Resolve symbol from DEXScreener token-pairs feed."""
        data = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        if not isinstance(pairs, list):
            return ""
        for pair in pairs[:20]:
            if not isinstance(pair, dict):
                continue
            base = pair.get("baseToken", {}) if isinstance(pair.get("baseToken"), dict) else {}
            quote = pair.get("quoteToken", {}) if isinstance(pair.get("quoteToken"), dict) else {}
            base_addr = self._normalize_address(base.get("address"))
            quote_addr = self._normalize_address(quote.get("address"))
            if base_addr == address:
                return (base.get("symbol") or "").strip().upper()
            if quote_addr == address:
                return (quote.get("symbol") or "").strip().upper()
        return ""

    async def _get_symbol_from_geckoterminal(self, session: aiohttp.ClientSession, address: str) -> str:
        """Resolve symbol from GeckoTerminal token endpoint."""
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/tokens/{address}"
        data = await self._fetch_json(session, url)
        token = data.get("data", {}) if isinstance(data, dict) else {}
        attrs = token.get("attributes", {}) if isinstance(token.get("attributes"), dict) else {}
        return (attrs.get("symbol") or "").strip().upper()

    async def _get_symbol_from_coingecko_contract(self, session: aiohttp.ClientSession, address: str) -> str:
        """Resolve symbol from CoinGecko contract endpoint."""
        url = f"https://api.coingecko.com/api/v3/coins/polygon-pos/contract/{address}"
        data = await self._fetch_json(session, url)
        if not isinstance(data, dict):
            return ""
        return (data.get("symbol") or "").strip().upper()

    async def _estimate_token_tvl_usd(self, session: aiohttp.ClientSession, address: str) -> float:
        """Estimate token TVL in USD using external liquidity feeds."""
        total_liquidity = 0.0

        dexscreener = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        pairs = dexscreener.get("pairs", []) if isinstance(dexscreener, dict) else []
        if isinstance(pairs, list):
            for pair in pairs[:50]:
                if not isinstance(pair, dict):
                    continue
                liquidity = pair.get("liquidity", {}) if isinstance(pair.get("liquidity"), dict) else {}
                usd = liquidity.get("usd")
                try:
                    total_liquidity += float(usd or 0.0)
                except Exception:
                    continue

        gecko_url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/tokens/{address}/pools"
        gecko = await self._fetch_json(session, gecko_url)
        pools = gecko.get("data", []) if isinstance(gecko, dict) else []
        if isinstance(pools, list):
            for pool in pools[:50]:
                if not isinstance(pool, dict):
                    continue
                attrs = pool.get("attributes", {}) if isinstance(pool.get("attributes"), dict) else {}
                reserve = attrs.get("reserve_in_usd")
                try:
                    total_liquidity += float(reserve or 0.0)
                except Exception:
                    continue

        return total_liquidity

    def _extract_polygon_dexes(self, llama_protocols_data: Any) -> Dict[str, str]:
        """Extract Polygon DEX-like protocol addresses from DefiLlama protocols feed."""
        dexes: Dict[str, str] = {}
        if not isinstance(llama_protocols_data, list):
            return dexes

        for protocol in llama_protocols_data:
            if not isinstance(protocol, dict):
                continue
            category = str(protocol.get("category") or "").lower()
            if category not in {"dexes", "dex aggregator"}:
                continue
            name = str(protocol.get("name") or "").strip().lower()
            address = self._normalize_address(protocol.get("address"))
            chains = protocol.get("chains", [])
            chain_ok = False
            if isinstance(chains, list):
                chain_ok = any(str(c).lower() == "polygon" for c in chains)
            if name and address and chain_ok:
                dexes[name] = address
        return dexes

    def _filter_and_limit_tokens(self, tokens: Dict[str, Dict[str, str]], max_tokens: int) -> Dict[str, Dict[str, str]]:
        """Keep high-confidence tokens and preserve high-TVL unresolved tokens after five discovery attempts."""
        filtered: Dict[str, Dict[str, str]] = {}
        for address, token in tokens.items():
            symbol = (token.get("symbol") or "").strip().upper()
            normalized = self._normalize_address(address)
            tvl_usd = float(token.get("tvl_usd", 0.0) or 0.0)
            attempts = int(token.get("discovery_attempts", 0) or 0)
            if not normalized or not symbol:
                continue
            if symbol in {"UNKNOWN", "N/A", "-", "?"}:
                if not (tvl_usd >= self.MIN_TOKEN_TVL_USD and attempts >= self.MIN_DISCOVERY_ATTEMPTS):
                    continue
            filtered[normalized] = {
                "address": normalized,
                "symbol": symbol,
                "tvl_usd": 0.0,
                "discovery_attempts": attempts,
            }
            if len(filtered) >= max_tokens:
                break
        return filtered

    def _normalize_address(self, address: Any) -> str:
        """Normalize and validate EVM addresses."""
        if not isinstance(address, str) or not address:
            return ""
        address = address.strip()
        if not Web3.is_address(address):
            return ""
        try:
            return Web3.to_checksum_address(address)
        except Exception:
            return ""

    async def scan_all_dexes(self, tokens: List[Any]) -> List[Pool]:
        """Scan all DEXes for pools containing specified tokens.

        Primary policy:
        - onchain_v2 is the default source
        - DEXScreener is optional fallback/enrichment only
        - execution must never depend on DEXScreener priceUsd
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

        if getattr(self, "discovery_source", "onchain_v2") in ("onchain", "onchain_v2", "rpc"):
            try:
                raw_pools = await discover_v2_pools_onchain(
                    normalized_tokens,
                    factories=getattr(self, "_all_dexes", {}),
                    max_tokens=getattr(self, "onchain_discovery_max_tokens", 80),
                    max_pairs=getattr(self, "onchain_discovery_max_pairs", 2500),
                    concurrency=getattr(self, "onchain_discovery_concurrency", 32),
                )
                all_pools = [self._pool_from_onchain_v2(p) for p in raw_pools]

                # Discovery may observe all supported/unsupported families.
                # Execution must later enforce execution_supported=True.
                # For now we keep all discovered pools but mark math_mode/pool_type explicitly.
                self.last_scan_terminal_state = "POOLS_DISCOVERED" if all_pools else "NO_POOLS_DISCOVERED"
                logger.info(
                    "On-chain V2 discovery terminal_state=%s pools=%d source=%s",
                    self.last_scan_terminal_state,
                    len(all_pools),
                    self.discovery_source,
                )
                return all_pools
            except Exception as exc:
                self.scanner_errors += 1
                logger.exception("On-chain V2 discovery failed: %s", exc)
                if not getattr(self, "use_dexscreener", False):
                    self.last_scan_terminal_state = "REJECTED_BY_ONCHAIN_DISCOVERY_FAILURE"
                    return []

        if not getattr(self, "use_dexscreener", False):
            self.last_scan_terminal_state = "NO_POOLS_DISCOVERED"
            logger.warning("DEXScreener disabled and on-chain discovery returned no pools")
            return []

        logger.warning("Using DEXScreener fallback because USE_DEXSCREENER=true")

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

    def _normalize_tokens(self, tokens: List[Any]) -> List[Dict[str, str]]:
        """Convert incoming token list to validated address/symbol dicts."""
        normalized: List[Dict[str, str]] = []
        for token in tokens:
            address = ""
            symbol = ""
            if isinstance(token, dict):
                address = self._normalize_address(token.get("address"))
                symbol = (token.get("symbol") or "").strip().upper()
            elif isinstance(token, str):
                address = self._normalize_address(token)
                symbol = self.token_metadata.get(address, {}).get("symbol", "")

            if not address:
                continue
            if not symbol:
                symbol = self.token_metadata.get(address, {}).get("symbol", "")
            if not symbol:
                token_meta = self.token_metadata.get(address, {})
                tvl_usd = float(token_meta.get("tvl_usd", 0.0) or 0.0)
                attempts = int(token_meta.get("discovery_attempts", 0) or 0)
                if tvl_usd >= self.MIN_TOKEN_TVL_USD and attempts >= self.MIN_DISCOVERY_ATTEMPTS:
                    symbol = f"UNK_{address[-6:]}"
                else:
                    continue

            normalized.append({"address": address, "symbol": symbol})
        return normalized



    def _token_decimals_for_tvl(self, token_address: str) -> int:
        meta = getattr(self, "token_metadata", {}) or {}
        key = token_address.lower()
        item = meta.get(key) or meta.get(token_address)
        if isinstance(item, dict):
            try:
                return int(item.get("decimals", 18))
            except Exception:
                return 18
        if hasattr(item, "decimals"):
            try:
                return int(item.decimals)
            except Exception:
                return 18
        return 18

    def _token_usd_price_for_tvl(self, token_address: str) -> float | None:
        """Return USD price for known tokens. Unknown = None, not zero."""
        meta = getattr(self, "token_metadata", {}) or {}
        key = token_address.lower()
        item = meta.get(key) or meta.get(token_address)

        for field in ("price_usd", "usd_price", "price", "derived_usd"):
            if isinstance(item, dict) and item.get(field) is not None:
                try:
                    v = float(item.get(field))
                    return v if v > 0 else None
                except Exception:
                    pass
            if hasattr(item, field):
                try:
                    v = float(getattr(item, field))
                    return v if v > 0 else None
                except Exception:
                    pass

        # Polygon canonical/common stable + blue-chip fallback anchors.
        anchors = {
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 1.0, # USDC.e
            "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": 1.0, # native USDC
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 1.0, # USDT
            "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": 1.0, # DAI
        }
        return anchors.get(key)

    def _compute_pool_tvl_usd_from_reserves(
        self,
        token0: str,
        token1: str,
        reserve0_raw: int,
        reserve1_raw: int,
    ) -> tuple[float, bool, dict]:
        d0 = self._token_decimals_for_tvl(token0)
        d1 = self._token_decimals_for_tvl(token1)

        r0 = float(reserve0_raw) / float(10 ** d0)
        r1 = float(reserve1_raw) / float(10 ** d1)

        p0 = self._token_usd_price_for_tvl(token0)
        p1 = self._token_usd_price_for_tvl(token1)

        side0 = r0 * p0 if p0 is not None else None
        side1 = r1 * p1 if p1 is not None else None

        if side0 is not None and side1 is not None:
            tvl = side0 + side1
            verified = True
        elif side0 is not None:
            tvl = side0 * 2.0
            verified = False
        elif side1 is not None:
            tvl = side1 * 2.0
            verified = False
        else:
            tvl = 0.0
            verified = False

        return tvl, verified, {
            "token0_decimals": d0,
            "token1_decimals": d1,
            "token0_amount": r0,
            "token1_amount": r1,
            "token0_usd": p0,
            "token1_usd": p1,
            "side0_usd": side0,
            "side1_usd": side1,
        }

    def _pool_from_onchain_v2(self, raw: OnchainV2Pool) -> Pool:
        reserve0 = float(raw.reserve0)
        reserve1 = float(raw.reserve1)

        classified = classify_pool_kwargs(
            chain_id=137,
            dex_name=raw.dex_name,
            factory_address=raw.factory,
            pool_address=raw.pair_address,
            token0=raw.token0,
            token1=raw.token1,
            reserve0=raw.reserve0,
            reserve1=raw.reserve1,
            source="onchain_v2",
        )

        meta = {
            "address": raw.pair_address,
            "pool_address": raw.pair_address,
            "pair_address": raw.pair_address,
            "dex": classified.dex_name,
            "dex_name": classified.dex_name,
            "token0": raw.token0,
            "token1": raw.token1,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "reserves0": reserve0,
            "reserves1": reserve1,
            "block_number": raw.block_number or 0,
            "tvl_usd": 0.0,
            "liquidity_usd": 0.0,
            "fee": (classified.fee_bps or 30) / 10000,
            "fee_bps": classified.fee_bps or 30,
            "fee_tier": classified.fee_tier,
            "pool_type": classified.pool_family.value,
            "math_mode": classified.math_mode.value,
            "router_type": classified.router_type,
            "quote_engine": classified.quote_engine,
            "calldata_engine": classified.calldata_engine,
            "execution_supported": classified.execution_supported,
            "source": classified.source,
        }

        tvl_usd, tvl_verified, tvl_components = self._compute_pool_tvl_usd_from_reserves(
            raw.token0,
            raw.token1,
            raw.reserve0,
            raw.reserve1,
        )
        meta["tvl_usd"] = tvl_usd
        meta["liquidity_usd"] = tvl_usd
        meta["tvl_verified"] = tvl_verified
        meta["tvl_components"] = tvl_components

        allowed = set(inspect.signature(Pool).parameters.keys())
        kwargs = {k: v for k, v in meta.items() if k in allowed}

        try:
            pool = Pool(**kwargs)
        except TypeError:
            fallback_sets = (
                ("address", "dex", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("address", "dex", "token0", "token1", "reserve0", "reserve1"),
                ("pool_address", "dex_name", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("pair_address", "dex_name", "token0", "token1", "reserve0", "reserve1"),
            )
            pool = None
            last = None
            for keys in fallback_sets:
                try:
                    pool = Pool(**{k: meta[k] for k in keys if k in allowed})
                    break
                except TypeError as exc:
                    last = exc
            if pool is None:
                raise last or TypeError("Unable to construct Pool")

        for k, v in meta.items():
            try: setattr(pool, k, v)
            except Exception: pass

        return pool

    async def _scan_dex_pools(self, dex_name: str, factory: str, tokens: List[Dict[str, str]]) -> List[Pool]:
        """Scan pools for a specific DEX using live token-pair metadata where available."""
        _ = factory
        pools: List[Pool] = []

        dex_aliases = {
            "uniswap": {"uniswap", "uniswap-v3"},
            "quickswap": {"quickswap", "quickswap-v2", "quickswap-v3"},
            "sushiswap": {"sushiswap", "sushi"},
            "apeswap": {"apeswap"},
            "dfyn": {"dfyn"},
            "jetswap": {"jetswap"},
        }
        accepted = dex_aliases.get(dex_name.lower(), {dex_name.lower()})

        for token in tokens:
            addr = token.get("address")
            if not addr:
                continue

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
                dex_id = str(pair.get("dexId") or "").lower()
                if dex_id not in accepted:
                    continue

                pair_addr = self._normalize_address(pair.get("pairAddress"))
                if not pair_addr:
                    continue

                base = pair.get("baseToken", {}) if isinstance(pair.get("baseToken"), dict) else {}
                quote = pair.get("quoteToken", {}) if isinstance(pair.get("quoteToken"), dict) else {}
                token0 = self._normalize_address(base.get("address"))
                token1 = self._normalize_address(quote.get("address"))
                if not token0 or not token1:
                    continue

                liquidity = pair.get("liquidity", {}) if isinstance(pair.get("liquidity"), dict) else {}
                try:
                    tvl_usd = float(liquidity.get("usd") or 0.0)
                except Exception:
                    tvl_usd = 0.0
                if tvl_usd <= 0.0:
                    continue

                pair_label = str(pair.get("pairLabel") or "")
                fee = self._extract_fee_from_pair(pair_label)

                try:
                    mid_price = float(pair.get("priceUsd") or 0.0)
                except Exception:
                    mid_price = 0.0

                pools.append(
                    Pool(
                        address=pair_addr,
                        dex=dex_name,
                        token0=token0,
                        token1=token1,
                        tvl_usd=tvl_usd,
                        fee=fee,
                        mid_price_usd=mid_price,
                        data_source="dexscreener",
                        pool_type="v3" if dex_name in self.v3_dexes else "v2",
                    )
                )

        return pools

    async def _fetch_live_pairs_for_token(self, address: str) -> List[Dict[str, Any]]:
        """Fetch and cache external pair metadata for a token.

        DEXScreener is disabled by default.
        Reason:
        - external API 429s under 500-token scans
        - not authoritative for execution pricing
        - final execution must use on-chain reserves / pool state

        Enable only with USE_DEXSCREENER=true for dashboard enrichment or fallback diagnostics.
        """
        if address in self._token_pool_cache:
            cached = self._token_pool_cache.get(address)
            return cached if isinstance(cached, list) else []

        if not getattr(self, "use_dexscreener", False):
            self._token_pool_cache[address] = []
            return []

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

    def _extract_fee_from_pair(self, pair_label: str) -> float:
        """Best-effort extraction of fee from pair label; defaults to 0.3%."""
        lowered = pair_label.lower()
        if "0.01%" in lowered:
            return 0.0001
        if "0.05%" in lowered:
            return 0.0005
        if "0.3%" in lowered:
            return 0.003
        if "1%" in lowered:
            return 0.01
        return 0.003

    async def get_price(self, pool: Pool, token_in: str, amount_in: float) -> float:
        """Get output amount for a swap on a pool"""
        # Mock price calculation - in real implementation, call pool contract
        if token_in == pool.token0:
            # Assume 1:1 for simplicity, in reality calculate using AMM formula
            return amount_in * 0.997  # After 0.3% fee
        else:
            return amount_in / 0.997

class ArbitrageDetector:
    """Detect arbitrage opportunities across DEXes"""

    def __init__(self, dex_monitor: PolygonDEXMonitor, flash_config: FlashLoanConfig):
        self.dex_monitor = dex_monitor
        self.flash_config = flash_config

    async def find_opportunities(self, tokens: List[Any], min_spread_bps: float = 50) -> List[ArbitrageOpportunity]:
        """Find arbitrage opportunities across all DEXes."""
        pools = await self.dex_monitor.scan_all_dexes(tokens)
        opportunities = []

        normalized_tokens = self.dex_monitor._normalize_tokens(tokens)
        for token in normalized_tokens:
            addr = token.get("address")
            symbol = token.get("symbol")
            if not addr:
                continue
            token_pools = [p for p in pools if p.token0 == addr or p.token1 == addr]

            if len(token_pools) < 2:
                continue

            flash_amount = self._flash_loan_size_for_token(token_pools)
            if flash_amount <= 0:
                continue

            # Build executable buy/sell quotes for the same flash-loan size C.
            quotes = []
            for pool in token_pools:
                try:
                    buy_price = await self._get_effective_price(pool, addr, flash_amount, side="buy")
                    sell_price = await self._get_effective_price(pool, addr, flash_amount, side="sell")
                    if buy_price > 0 and sell_price > 0:
                        quotes.append((pool, buy_price, sell_price))
                except Exception as e:
                    logger.error(f"Error getting price for {pool.address}: {e}")

            if len(quotes) < 2:
                continue

            buy_quotes = sorted(((pool, buy_price) for pool, buy_price, _ in quotes), key=lambda x: x[1])
            sell_quotes = sorted(((pool, sell_price) for pool, _, sell_price in quotes), key=lambda x: x[1], reverse=True)

            pair = self._select_entry_exit_pools(buy_quotes, sell_quotes)
            if pair is None:
                continue

            cheapest_pool, entry_price_usd, expensive_pool, exit_price_usd = pair

            spread_bps = self._compute_spread_bps(entry_price_usd, exit_price_usd)
            if spread_bps is None:
                continue

            if spread_bps > min_spread_bps:
                opportunity = await self._create_opportunity(
                    symbol or addr,
                    cheapest_pool,
                    expensive_pool,
                    entry_price_usd,
                    exit_price_usd,
                    spread_bps,
                    flash_amount,
                )
                if opportunity:
                    opportunities.append(opportunity)

        return opportunities

    def _flash_loan_size_for_token(self, token_pools: List[Pool]) -> float:
        """Choose one flash-loan size C used for both entry and exit quote evaluation.

        Sized as ``max_pool_tvl_percent`` of the smallest TVL among all pools
        that contain this token, ensuring the loan never exceeds what the
        weakest pool in the swap can absorb.

        ``max_pool_tvl_percent`` defaults to 0.10 (10 %) in ``FlashLoanConfig``.
        Keep it at or below 0.10 â€” higher fractions cause excessive price impact
        in the weakest pool and increase the risk of failed or reverted transactions.
        """
        min_tvl = min(float(pool.tvl_usd) for pool in token_pools)
        max_loan = min_tvl * min(self.flash_config.max_pool_tvl_percent, 0.10)
        return max(self.flash_config.min_amount_usd, max_loan)

    def _select_entry_exit_pools(
        self,
        buy_quotes: List[Any],
        sell_quotes: List[Any],
    ) -> Optional[tuple[Pool, float, Pool, float]]:
        """Pick lowest buy and highest sell on distinct pools for the same token."""
        for buy_pool, buy_price in buy_quotes:
            for sell_pool, sell_price in sell_quotes:
                if buy_pool.address != sell_pool.address:
                    return buy_pool, buy_price, sell_pool, sell_price
        return None
    async def _get_effective_price(self, pool: Pool, token: str, amount_in_usd: float, side: str) -> float:
        """Get executable quote price for the given side using amount C.

        `side="buy"` returns ask-like entry price.
        `side="sell"` returns bid-like exit price.
        """
        # Best-effort executable quote model from observed mid price + fee + impact.
        _ = token
        base_price = float(pool.mid_price_usd) if pool.mid_price_usd > 0 else 1.0
        impact = min(0.02, max(0.0, float(amount_in_usd) / max(float(pool.tvl_usd), 1.0)))
        if side == "buy":
            return base_price * (1 + float(pool.fee) + impact)
        if side == "sell":
            return base_price * max(0.0, 1 - float(pool.fee) - impact)
        raise ValueError(f"Unsupported quote side: {side}")

    def _compute_spread_bps(self, buy_price: float, sell_price: float) -> Optional[float]:
        """Compute spread only from explicit entry (buy) and exit (sell) prices."""
        if not math.isfinite(buy_price) or not math.isfinite(sell_price):
            return None
        if buy_price <= 0 or sell_price <= 0:
            return None
        if sell_price <= buy_price:
            return None
        return ((sell_price - buy_price) / buy_price) * 10000.0

    async def _create_opportunity(
        self,
        token: str,
        buy_pool: Pool,
        sell_pool: Pool,
        buy_price: float,
        sell_price: float,
        spread_bps: float,
        flash_amount: float,
    ) -> Optional[ArbitrageOpportunity]:
        """Create arbitrage opportunity with flash loan sizing"""
        # Estimate profit from explicit entry/exit prices.
        token_amount = flash_amount / buy_price
        gross_profit = (sell_price - buy_price) * token_amount
        estimated_profit = gross_profit * 0.9  # After fees and slippage

        if estimated_profit > 10:  # Minimum $10 profit
            return ArbitrageOpportunity(
                token=token,
                buy_pool=buy_pool,
                sell_pool=sell_pool,
                buy_price=buy_price,
                sell_price=sell_price,
                spread_bps=spread_bps,
                estimated_profit_usd=estimated_profit,
                flash_loan_amount=flash_amount,
                flash_loan_token=token,
                path=[buy_pool.address, sell_pool.address],  # Simple 2-hop
                gas_estimate=0.25
            )
        return None
