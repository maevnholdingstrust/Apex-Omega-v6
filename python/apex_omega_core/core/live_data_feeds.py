"""Live data feed aggregator for Apex-Omega-v6.

Polls four public data sources on demand:

  * The Graph — Uniswap V3 subgraph on Polygon (pool reserves, fees, TVL)
  * Polygon RPC — block number and gas price via eth_gasPrice
  * CoinGecko free API — token USD prices
  * PolygonScan gas oracle — base fee / mempool estimates

Design rules
------------
* No mock fallback.  If a feed is unavailable its status becomes
  ``"FEED ERROR"`` and callers receive ``None`` for that feed's data.
* All four feeds are polled concurrently via ``asyncio.gather``.
* CPMM arbitrage signals are computed only when *both* The Graph and
  CoinGecko feeds are LIVE; otherwise ``arb_signals`` is an empty list.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from web3 import Web3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public endpoints (all free-tier, no auth required except PolygonScan key)
# ---------------------------------------------------------------------------

_GRAPH_POLYGON_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-polygon"
)

_COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=matic-network,weth,wrapped-bitcoin,aave,chainlink,uniswap"
    ",the-sandbox,decentraland,curve-dao-token,balancer,sushi"
    ",compound-governance-token,maker"
    "&vs_currencies=usd"
)

_POLYGONSCAN_GAS_URL = (
    "https://api.polygonscan.com/api?module=gastracker&action=gasoracle"
)

# Trade size used for CPMM arbitrage profit estimates: 0.1 % of the smaller
# pool's TVL.  Large enough to produce meaningful signals; small enough to
# remain within the linear region of the constant-product curve.
_CPMM_TRADE_SIZE_FRACTION = 0.001

# GraphQL query: top 20 Uniswap V3 Polygon pools by TVL (>$100k)
_GRAPH_POOL_QUERY = """
{
  pools(
    first: 20,
    orderBy: totalValueLockedUSD,
    orderDirection: desc,
    where: { totalValueLockedUSD_gt: "100000" }
  ) {
    id
    token0 { symbol decimals }
    token1 { symbol decimals }
    feeTier
    sqrtPrice
    liquidity
    totalValueLockedUSD
    token0Price
    token1Price
  }
}
"""

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class FeedState:
    """State of a single external data feed."""

    name: str
    status: str              # "LIVE" or "FEED ERROR"
    fetched_at: float        # unix timestamp of last successful or failed poll
    latency_ms: float        # round-trip latency
    data: Optional[Any]      # raw parsed payload; None on error
    error: Optional[str]     # error message; None on success

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "fetched_at": self.fetched_at,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
        }


@dataclass
class PoolReserveSnapshot:
    """CPMM-approximated pool state derived from The Graph V3 data."""

    pool_id: str
    sym0: str
    sym1: str
    fee_tier: int        # micro-units: 3000 = 0.3 %, 500 = 0.05 %
    tvl_usd: float
    token0_price: float  # token1 per token0
    token1_price: float  # token0 per token1


@dataclass
class ArbitrageSignal:
    """CPMM arbitrage signal computed from live pool reserves."""

    pair: str
    buy_pool_id: str
    sell_pool_id: str
    fee_buy: float
    fee_sell: float
    price_buy: float
    price_sell: float
    spread_bps: float
    tvl_buy_usd: float
    tvl_sell_usd: float
    cpmm_arb_profit_usd: float  # estimated profit at 0.1 % TVL trade size


@dataclass
class LiveFeedSnapshot:
    """Aggregated snapshot of all four live feeds plus derived signals."""

    timestamp: float
    feeds: Dict[str, FeedState]
    pools: List[PoolReserveSnapshot]
    token_prices_usd: Dict[str, float]
    gas_base_fee_gwei: Optional[float]
    gas_safe_gwei: Optional[float]
    gas_fast_gwei: Optional[float]
    block_number: Optional[int]
    rpc_gas_price_gwei: Optional[float]
    arb_signals: List[ArbitrageSignal]
    all_live: bool


# ---------------------------------------------------------------------------
# Feed poller
# ---------------------------------------------------------------------------


class LiveDataFeeds:
    """Polls four public data sources concurrently and tracks their status.

    Parameters
    ----------
    rpc_url:
        Polygon HTTP-RPC endpoint (defaults to ``POLYGON_RPC`` env var or
        the public ``polygon-rpc.com`` endpoint).
    polygonscan_api_key:
        Optional PolygonScan API key (``POLYGONSCAN_API_KEY`` env var).
        Without a key the free unauthenticated endpoint is used; rate-limited
        to 5 req/s.  Absent a key, the feed still works unless the rate limit
        is exceeded.
    graph_url:
        The Graph subgraph endpoint.  Override for testing or self-hosted
        Graph nodes.
    timeout_s:
        HTTP timeout for external API calls (default 10 s).
    """

    def __init__(
        self,
        rpc_url: str = "",
        polygonscan_api_key: str = "",
        graph_url: str = _GRAPH_POLYGON_SUBGRAPH,
        timeout_s: float = 10.0,
    ) -> None:
        self._rpc_url = rpc_url or os.getenv("POLYGON_RPC", "https://polygon-rpc.com/")
        self._polygonscan_key = polygonscan_api_key or os.getenv(
            "POLYGONSCAN_API_KEY", ""
        )
        self._graph_url = graph_url
        self._timeout_s = timeout_s
        self._w3 = Web3(
            Web3.HTTPProvider(self._rpc_url, request_kwargs={"timeout": 8})
        )
        self._last_snapshot: Optional[LiveFeedSnapshot] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def poll(self) -> LiveFeedSnapshot:
        """Poll all four feeds concurrently and return an aggregated snapshot.

        Never raises — each individual feed failure is captured as a
        ``FeedState(status="FEED ERROR")`` entry in the returned snapshot.
        """
        t_start = time.time()
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_s)
        ) as session:
            graph_state, coingecko_state, etherscan_state, rpc_state = (
                await asyncio.gather(
                    self._poll_graph(session),
                    self._poll_coingecko(session),
                    self._poll_etherscan_gas(session),
                    self._poll_rpc(),
                )
            )

        feeds: Dict[str, FeedState] = {
            "the_graph": graph_state,
            "coingecko": coingecko_state,
            "etherscan_gas": etherscan_state,
            "polygon_rpc": rpc_state,
        }

        pools = (
            self._parse_graph_pools(graph_state)
            if graph_state.status == "LIVE"
            else []
        )
        token_prices = (
            self._parse_coingecko_prices(coingecko_state)
            if coingecko_state.status == "LIVE"
            else {}
        )
        gas_base, gas_safe, gas_fast = (
            self._parse_etherscan_gas(etherscan_state)
            if etherscan_state.status == "LIVE"
            else (None, None, None)
        )
        rpc_data = rpc_state.data or {}
        block_number = rpc_data.get("block_number") if rpc_state.status == "LIVE" else None
        rpc_gas_gwei = (
            rpc_data.get("gas_price_gwei") if rpc_state.status == "LIVE" else None
        )

        arb_signals = (
            self._compute_cpmm_arb_signals(pools, token_prices)
            if pools and token_prices
            else []
        )

        snapshot = LiveFeedSnapshot(
            timestamp=t_start,
            feeds=feeds,
            pools=pools,
            token_prices_usd=token_prices,
            gas_base_fee_gwei=gas_base,
            gas_safe_gwei=gas_safe,
            gas_fast_gwei=gas_fast,
            block_number=block_number,
            rpc_gas_price_gwei=rpc_gas_gwei,
            arb_signals=arb_signals,
            all_live=all(f.status == "LIVE" for f in feeds.values()),
        )
        self._last_snapshot = snapshot
        return snapshot

    def last_snapshot(self) -> Optional[LiveFeedSnapshot]:
        """Return the most recently polled snapshot, or None if never polled."""
        return self._last_snapshot

    # ------------------------------------------------------------------
    # Individual feed pollers (each returns FeedState, never raises)
    # ------------------------------------------------------------------

    async def _poll_graph(self, session: aiohttp.ClientSession) -> FeedState:
        """Query Uniswap V3 subgraph for top pools by TVL."""
        t0 = time.monotonic()
        try:
            async with session.post(
                self._graph_url,
                json={"query": _GRAPH_POOL_QUERY},
                headers={"Content-Type": "application/json"},
            ) as resp:
                latency_ms = (time.monotonic() - t0) * 1000.0
                if resp.status != 200:
                    return FeedState(
                        name="the_graph",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error=f"HTTP {resp.status}",
                    )
                body = await resp.json(content_type=None)
                gql_errors = body.get("errors")
                if gql_errors:
                    msg = gql_errors[0].get("message", str(gql_errors[0]))
                    return FeedState(
                        name="the_graph",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error=str(msg)[:200],
                    )
                pools = (body.get("data") or {}).get("pools")
                if not pools:
                    return FeedState(
                        name="the_graph",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error="Subgraph returned empty pools list",
                    )
                return FeedState(
                    name="the_graph",
                    status="LIVE",
                    fetched_at=time.time(),
                    latency_ms=latency_ms,
                    data=pools,
                    error=None,
                )
        except Exception as exc:  # noqa: BLE001
            return FeedState(
                name="the_graph",
                status="FEED ERROR",
                fetched_at=time.time(),
                latency_ms=(time.monotonic() - t0) * 1000.0,
                data=None,
                error=str(exc)[:200],
            )

    async def _poll_coingecko(self, session: aiohttp.ClientSession) -> FeedState:
        """Fetch token USD prices from CoinGecko free API."""
        t0 = time.monotonic()
        try:
            async with session.get(_COINGECKO_PRICE_URL) as resp:
                latency_ms = (time.monotonic() - t0) * 1000.0
                if resp.status != 200:
                    return FeedState(
                        name="coingecko",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error=f"HTTP {resp.status}",
                    )
                data = await resp.json(content_type=None)
                if not data:
                    return FeedState(
                        name="coingecko",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error="Empty response from CoinGecko",
                    )
                return FeedState(
                    name="coingecko",
                    status="LIVE",
                    fetched_at=time.time(),
                    latency_ms=latency_ms,
                    data=data,
                    error=None,
                )
        except Exception as exc:  # noqa: BLE001
            return FeedState(
                name="coingecko",
                status="FEED ERROR",
                fetched_at=time.time(),
                latency_ms=(time.monotonic() - t0) * 1000.0,
                data=None,
                error=str(exc)[:200],
            )

    async def _poll_etherscan_gas(self, session: aiohttp.ClientSession) -> FeedState:
        """Fetch base fee / mempool estimates from PolygonScan gas oracle."""
        t0 = time.monotonic()
        url = _POLYGONSCAN_GAS_URL
        if self._polygonscan_key:
            url = f"{url}&apikey={self._polygonscan_key}"
        try:
            async with session.get(url) as resp:
                latency_ms = (time.monotonic() - t0) * 1000.0
                if resp.status != 200:
                    return FeedState(
                        name="etherscan_gas",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error=f"HTTP {resp.status}",
                    )
                body = await resp.json(content_type=None)
                if body.get("status") != "1" or not body.get("result"):
                    msg = body.get("message") or body.get("result") or "API error"
                    return FeedState(
                        name="etherscan_gas",
                        status="FEED ERROR",
                        fetched_at=time.time(),
                        latency_ms=latency_ms,
                        data=None,
                        error=str(msg)[:200],
                    )
                return FeedState(
                    name="etherscan_gas",
                    status="LIVE",
                    fetched_at=time.time(),
                    latency_ms=latency_ms,
                    data=body["result"],
                    error=None,
                )
        except Exception as exc:  # noqa: BLE001
            return FeedState(
                name="etherscan_gas",
                status="FEED ERROR",
                fetched_at=time.time(),
                latency_ms=(time.monotonic() - t0) * 1000.0,
                data=None,
                error=str(exc)[:200],
            )

    async def _poll_rpc(self) -> FeedState:
        """Fetch block number and gas price from the configured Polygon RPC."""
        t0 = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            block_number = await loop.run_in_executor(
                None, lambda: self._w3.eth.block_number
            )
            gas_price_wei = await loop.run_in_executor(
                None, lambda: self._w3.eth.gas_price
            )
            latency_ms = (time.monotonic() - t0) * 1000.0
            return FeedState(
                name="polygon_rpc",
                status="LIVE",
                fetched_at=time.time(),
                latency_ms=latency_ms,
                data={
                    "block_number": int(block_number),
                    "gas_price_gwei": float(gas_price_wei) / 1e9,
                },
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            return FeedState(
                name="polygon_rpc",
                status="FEED ERROR",
                fetched_at=time.time(),
                latency_ms=(time.monotonic() - t0) * 1000.0,
                data=None,
                error=str(exc)[:200],
            )

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_graph_pools(self, state: FeedState) -> List[PoolReserveSnapshot]:
        """Convert raw subgraph pool entries into PoolReserveSnapshot objects."""
        if not state.data:
            return []
        out: List[PoolReserveSnapshot] = []
        for p in state.data:
            try:
                tvl = float(p.get("totalValueLockedUSD") or 0)
                out.append(
                    PoolReserveSnapshot(
                        pool_id=p["id"],
                        sym0=(p["token0"].get("symbol") or "?").upper(),
                        sym1=(p["token1"].get("symbol") or "?").upper(),
                        fee_tier=int(p.get("feeTier") or 3000),
                        tvl_usd=tvl,
                        token0_price=float(p.get("token0Price") or 0),
                        token1_price=float(p.get("token1Price") or 0),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        return out

    def _parse_coingecko_prices(self, state: FeedState) -> Dict[str, float]:
        """Map CoinGecko IDs to token symbols and extract USD prices."""
        if not state.data:
            return {}
        # CoinGecko ID → list of token symbols used in this system
        _ID_MAP: Dict[str, List[str]] = {
            "matic-network":               ["WMATIC", "MATIC", "POL"],
            "weth":                        ["WETH", "ETH"],
            "wrapped-bitcoin":             ["WBTC", "BTC"],
            "aave":                        ["AAVE"],
            "chainlink":                   ["LINK"],
            "uniswap":                     ["UNI"],
            "the-sandbox":                 ["SAND"],
            "decentraland":                ["MANA"],
            "curve-dao-token":             ["CRV"],
            "balancer":                    ["BAL"],
            "sushi":                       ["SUSHI"],
            "compound-governance-token":   ["COMP"],
            "maker":                       ["MKR"],
        }
        # Stablecoins pegged at $1.00 (FRAX treated as stable; not fetched live)
        prices: Dict[str, float] = {
            "USDC": 1.0, "USDCe": 1.0, "USDT": 1.0,
            "DAI": 1.0,  "FRAX": 1.0,  "MAI": 1.0, "TUSD": 1.0,
        }
        for gecko_id, syms in _ID_MAP.items():
            entry = state.data.get(gecko_id)
            if isinstance(entry, dict):
                usd = entry.get("usd")
                if usd is not None:
                    for sym in syms:
                        prices[sym] = float(usd)
        return prices

    def _parse_etherscan_gas(
        self, state: FeedState
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (base_fee_gwei, safe_gwei, fast_gwei) from PolygonScan result."""
        if not state.data:
            return None, None, None
        result = state.data
        def _f(key: str) -> Optional[float]:
            try:
                v = result.get(key)
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        return _f("suggestBaseFee"), _f("SafeGasPrice"), _f("FastGasPrice")

    # ------------------------------------------------------------------
    # CPMM arbitrage math
    # ------------------------------------------------------------------

    def _compute_cpmm_arb_signals(
        self,
        pools: List[PoolReserveSnapshot],
        token_prices: Dict[str, float],
    ) -> List[ArbitrageSignal]:
        """Compute cross-pool spread and CPMM profit from live pool reserves.

        Algorithm
        ---------
        1. Group pools by canonical ``"sym0/sym1"`` pair key.
        2. For every (pool_a, pool_b) combination within a pair:
           a. Compute raw spread in bps from the two ``token0Price`` quotes.
           b. Skip pairs with spread < 1 bps.
           c. Estimate CPMM arbitrage profit using the constant-product
              output formula on 0.1 % of the smaller pool's TVL as the
              input trade size.
        3. Return the top 50 signals sorted by spread_bps descending.

        The TVL-based reserve approximation (reserve0 ≈ TVL/2 in USD,
        converted to token units via CoinGecko prices) is used because
        The Graph's V3 data does not expose active-range reserves directly.
        """
        pair_groups: Dict[str, List[PoolReserveSnapshot]] = {}
        for p in pools:
            key = f"{p.sym0}/{p.sym1}"
            pair_groups.setdefault(key, []).append(p)

        signals: List[ArbitrageSignal] = []

        for pair_key, group in pair_groups.items():
            if len(group) < 2:
                continue
            sym0, sym1 = pair_key.split("/")
            p0_usd = token_prices.get(sym0, 1.0)
            p1_usd = token_prices.get(sym1, 1.0)
            if p0_usd <= 0 or p1_usd <= 0:
                continue

            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pa, pb = group[i], group[j]
                    price_a = pa.token0_price  # sym1 per sym0 at pool a
                    price_b = pb.token0_price  # sym1 per sym0 at pool b

                    if price_a <= 0 or price_b <= 0:
                        continue

                    # Determine which pool is cheaper (buy there, sell other)
                    if price_a > price_b:
                        buy_pool, sell_pool = pb, pa
                        spread_bps = (price_a - price_b) / price_b * 10_000.0
                    else:
                        buy_pool, sell_pool = pa, pb
                        spread_bps = (price_b - price_a) / price_a * 10_000.0

                    if spread_bps < 1.0:
                        continue

                    # Trade size: fraction of smaller pool TVL
                    min_tvl_usd = min(buy_pool.tvl_usd, sell_pool.tvl_usd)
                    amount_in_usd = min_tvl_usd * _CPMM_TRADE_SIZE_FRACTION
                    amount_in_t0 = amount_in_usd / p0_usd

                    # CPMM approximation using TVL/2 as each side's reserve
                    fee_buy = buy_pool.fee_tier / 1_000_000.0
                    fee_sell = sell_pool.fee_tier / 1_000_000.0

                    r_in_a = (buy_pool.tvl_usd / 2.0) / p0_usd
                    r_out_a = (buy_pool.tvl_usd / 2.0) / p1_usd

                    arb_profit_usd = 0.0
                    if r_in_a > 0 and r_out_a > 0 and amount_in_t0 > 0:
                        # Leg 1: buy sym1 on buy_pool
                        dx1 = amount_in_t0 * (1.0 - fee_buy)
                        mid_t1 = r_out_a * dx1 / (r_in_a + dx1)

                        # Leg 2: sell sym1 on sell_pool
                        r_in_b = (sell_pool.tvl_usd / 2.0) / p1_usd
                        r_out_b = (sell_pool.tvl_usd / 2.0) / p0_usd
                        if r_in_b > 0 and r_out_b > 0:
                            dx2 = mid_t1 * (1.0 - fee_sell)
                            final_t0 = r_out_b * dx2 / (r_in_b + dx2)
                            arb_profit_usd = (final_t0 - amount_in_t0) * p0_usd

                    signals.append(
                        ArbitrageSignal(
                            pair=pair_key,
                            buy_pool_id=buy_pool.pool_id,
                            sell_pool_id=sell_pool.pool_id,
                            fee_buy=fee_buy,
                            fee_sell=fee_sell,
                            price_buy=buy_pool.token0_price,
                            price_sell=sell_pool.token0_price,
                            spread_bps=round(spread_bps, 2),
                            tvl_buy_usd=buy_pool.tvl_usd,
                            tvl_sell_usd=sell_pool.tvl_usd,
                            cpmm_arb_profit_usd=round(arb_profit_usd, 6),
                        )
                    )

        signals.sort(key=lambda s: s.spread_bps, reverse=True)
        return signals[:50]
