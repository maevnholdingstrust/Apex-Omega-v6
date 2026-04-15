import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp
from web3 import Web3
from .types import Pool, ArbitrageOpportunity, FlashLoanConfig

logger = logging.getLogger(__name__)

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
        self.dexes: Dict[str, str] = {
            "uniswap": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "quickswap": "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            "sushiswap": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            "apeswap": "0xCf083Be4164828f00cAE704EC15a36D711491284",
            "dfyn": "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B",
            "jetswap": "0x668ad0ed2622b0ac445205f25ee12a7d618cfb52",
        }
        self.token_metadata: Dict[str, Dict[str, str]] = {}
        self.pools: Dict[str, List[Pool]] = {}
        self._last_registry_refresh: float = 0.0
        self._registry_ttl_seconds: int = 1800

    async def refresh_market_registry(self, max_tokens: int = 300, force: bool = False) -> None:
        """Refresh token and DEX coverage from external sources with caching."""
        now = time.time()
        if not force and self.token_metadata and now - self._last_registry_refresh < self._registry_ttl_seconds:
            return

        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            oneinch_task = self._fetch_json(session, "https://api.1inch.io/v5.0/137/tokens")
            llama_tokens_task = self._fetch_json(session, "https://coins.llama.fi/blockchain/polygon")
            llama_protocols_task = self._fetch_json(session, "https://api.llama.fi/protocols")
            gecko_task = self._fetch_json(
                session,
                "https://api.coingecko.com/api/v3/coins/list?include_platform=true",
            )
            oneinch_data, llama_tokens_data, llama_protocols_data, gecko_data = await asyncio.gather(
                oneinch_task,
                llama_tokens_task,
                llama_protocols_task,
                gecko_task,
            )

        tokens = self._merge_token_sources(oneinch_data, llama_tokens_data, gecko_data)
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

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> Any:
        """Fetch JSON with robust error handling and fall back to empty structures."""
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning("Fetch failed (%s): status=%s", url, response.status)
                    return {}
                return await response.json(content_type=None)
        except Exception as e:
            logger.warning("Fetch failed (%s): %s", url, e)
            return {}

    def _merge_token_sources(self, oneinch_data: Any, llama_tokens_data: Any, gecko_data: Any) -> Dict[str, Dict[str, str]]:
        """Merge token metadata from multiple sources by address."""
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

        if isinstance(llama_tokens_data, dict):
            coins_map = llama_tokens_data.get("coins", {})
            if isinstance(coins_map, dict):
                for key, info in coins_map.items():
                    if not isinstance(info, dict):
                        continue
                    chain, _, addr = str(key).partition(":")
                    if chain.lower() != "polygon" or not addr:
                        continue
                    normalized = self._normalize_address(addr)
                    if not normalized:
                        continue
                    symbol = (info.get("symbol") or "").strip().upper()
                    existing = merged.get(
                        normalized,
                        {"address": normalized, "symbol": "", "tvl_usd": 0.0, "discovery_attempts": 0},
                    )
                    if symbol:
                        existing["symbol"] = symbol
                    existing["discovery_attempts"] = int(existing.get("discovery_attempts", 0)) + 1
                    merged[normalized] = existing

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
                    {"address": normalized, "symbol": "", "tvl_usd": 0.0, "discovery_attempts": 0},
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
                "tvl_usd": tvl_usd,
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
        """Scan all DEXes for pools containing specified tokens."""
        if not self.token_metadata:
            await self.refresh_market_registry()

        normalized_tokens = self._normalize_tokens(tokens)
        all_pools = []
        for dex_name, factory_address in self.dexes.items():
            if not dex_name or not factory_address:
                continue
            try:
                pools = await self._scan_dex_pools(dex_name, factory_address, normalized_tokens)
                all_pools.extend(pools)
                logger.info(f"Scanned {len(pools)} pools on {dex_name}")
            except Exception as e:
                logger.error(f"Error scanning {dex_name}: {e}")
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

    async def _scan_dex_pools(self, dex_name: str, factory: str, tokens: List[Dict[str, str]]) -> List[Pool]:
        """Scan pools for a specific DEX using validated token metadata."""
        # This would integrate with DEX subgraph or on-chain calls
        # For now, return mock data
        pools = []
        for token in tokens:
            addr = token.get("address")
            symbol = token.get("symbol")
            if not addr or not symbol:
                continue
            # Mock pool data - in real implementation, query subgraph
            pool = Pool(
                address=f"0x{addr[2:10]}{dex_name[:8]}",
                dex=dex_name,
                token0=addr,
                token1="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC on Polygon
                tvl_usd=1000000.0,  # Mock TVL
                fee=0.003  # 0.3%
            )
            pools.append(pool)
        return pools

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

            # Find best buy and sell prices
            prices = []
            for pool in token_pools:
                try:
                    # Mock price data - in reality, get real-time prices
                    price = await self._get_effective_price(pool, addr)
                    prices.append((pool, price))
                except Exception as e:
                    logger.error(f"Error getting price for {pool.address}: {e}")

            if len(prices) < 2:
                continue

            # Sort by price
            prices.sort(key=lambda x: x[1])
            cheapest_pool, buy_price = prices[0]
            expensive_pool, sell_price = prices[-1]

            spread_bps = ((sell_price - buy_price) / buy_price) * 10000

            if spread_bps > min_spread_bps:
                opportunity = await self._create_opportunity(
                    symbol or addr, cheapest_pool, expensive_pool, buy_price, sell_price, spread_bps
                )
                if opportunity:
                    opportunities.append(opportunity)

        return opportunities

    async def _get_effective_price(self, pool: Pool, token: str) -> float:
        """Get effective price considering fees"""
        # Mock implementation
        base_price = 1.0  # Assume $1 per token for simplicity
        return base_price * (1 + pool.fee)  # Price after fees

    async def _create_opportunity(self, token: str, buy_pool: Pool, sell_pool: Pool,
                                buy_price: float, sell_price: float, spread_bps: float) -> Optional[ArbitrageOpportunity]:
        """Create arbitrage opportunity with flash loan sizing"""
        # Calculate optimal flash loan amount
        min_tvl = min(buy_pool.tvl_usd, sell_pool.tvl_usd)
        max_loan = min_tvl * self.flash_config.max_pool_tvl_percent
        flash_amount = max(self.flash_config.min_amount_usd, max_loan * 0.1)  # Start with 10% of max

        # Estimate profit
        gross_profit = (sell_price - buy_price) * flash_amount / buy_price
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
                gas_estimate=0.1  # Mock gas cost
            )
        return None