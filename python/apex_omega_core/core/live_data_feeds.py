"""Live data feed aggregator for Apex-Omega-v6.

Polls four public data sources on demand:

  * The Graph — Uniswap V3 subgraph per chain (pool reserves, fees, TVL)
  * Chain RPC  — block number and gas price via eth_gasPrice (one per chain)
  * CoinGecko free API — token USD prices (ONE call shared across all chains)
  * PolygonScan / Etherscan gas oracle — per chain where available

Design rules
------------
* Server-side TTL cache (``APEX_FEED_CACHE_TTL_S``, default 30 s).  Fresh
  network calls are only made when the cache is stale — the browser polls
  every 5 s but the backend only hits external APIs once per TTL window.
* Stale-data fallback (``APEX_FEED_STALE_TTL_S``, default 300 s).  When a
  feed returns an error the last known-good snapshot is returned with status
  ``"STALE"`` instead of ``"FEED ERROR"``, so the dashboard always has data.
* Multi-chain: the feeder auto-detects chains from environment variables.
  ``POLYGON_RPC`` enables Polygon (always included as primary).  Setting any
  of ``ETHEREUM_RPC``, ``ARBITRUM_RPC``, ``OPTIMISM_RPC``, ``BSC_RPC``, or
  ``AVAX_RPC`` adds that chain to the polling set.  CoinGecko is called
  exactly ONCE regardless of how many chains are configured.
* All per-chain RPC polls run concurrently via ``asyncio.gather``.
* CPMM arbitrage signals are computed only when *both* The Graph and
  CoinGecko feeds are LIVE or STALE; otherwise ``arb_signals`` is an empty
  list.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from web3 import Web3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache / stale-fallback tunables (override via env)
# ---------------------------------------------------------------------------

#: How long a polled snapshot is considered fresh before the next real poll.
_CACHE_TTL_S: float = float(os.getenv("APEX_FEED_CACHE_TTL_S", "30"))

#: How long a last-known-good feed state is served as STALE before expiring.
_STALE_FALLBACK_TTL_S: float = float(os.getenv("APEX_FEED_STALE_TTL_S", "300"))

# ---------------------------------------------------------------------------
# Public endpoints (all free-tier, no auth required except block-explorer key)
# ---------------------------------------------------------------------------

_GRAPH_POLYGON_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-polygon"
)
_GRAPH_ETHEREUM_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
)
_GRAPH_ARBITRUM_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/ianlapham/arbitrum-minimal"
)
_GRAPH_OPTIMISM_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/ianlapham/optimism-post-regenesis"
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
_ETHERSCAN_GAS_URL = (
    "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
)

# ---------------------------------------------------------------------------
# Per-chain static configuration
# ---------------------------------------------------------------------------

#: Chain definitions: maps a chain slug to its static config.
#: ``rpc_env`` is the primary env var; ``rpc_fallbacks`` are tried in order
#: when the primary is absent; ``rpc_public`` is the final free-tier fallback.
_CHAIN_DEFS: Dict[str, Dict[str, Any]] = {
    "polygon": {
        "chain_id": 137,
        "label": "Polygon",
        "rpc_env": "POLYGON_RPC",
        "rpc_fallbacks": ["ALCHEMY_HTTP_1", "ALCHEMY_HTTP_2", "INFURA_HTTP"],
        "rpc_public": "https://polygon.drpc.org",
        "graph_url": _GRAPH_POLYGON_SUBGRAPH,
        "gas_url": _POLYGONSCAN_GAS_URL,
        "gas_key_env": "POLYGONSCAN_API_KEY",
        "always_include": True,
    },
    "ethereum": {
        "chain_id": 1,
        "label": "Ethereum",
        "rpc_env": "ETHEREUM_RPC",
        "rpc_fallbacks": [],
        "rpc_public": "https://eth.drpc.org",
        "graph_url": _GRAPH_ETHEREUM_SUBGRAPH,
        "gas_url": _ETHERSCAN_GAS_URL,
        "gas_key_env": "ETHERSCAN_API_KEY",
        "always_include": False,
    },
    "arbitrum": {
        "chain_id": 42161,
        "label": "Arbitrum",
        "rpc_env": "ARBITRUM_RPC",
        "rpc_fallbacks": [],
        "rpc_public": "https://arbitrum.drpc.org",
        "graph_url": _GRAPH_ARBITRUM_SUBGRAPH,
        "gas_url": None,
        "gas_key_env": None,
        "always_include": False,
    },
    "optimism": {
        "chain_id": 10,
        "label": "Optimism",
        "rpc_env": "OPTIMISM_RPC",
        "rpc_fallbacks": [],
        "rpc_public": "https://optimism.drpc.org",
        "graph_url": _GRAPH_OPTIMISM_SUBGRAPH,
        "gas_url": None,
        "gas_key_env": None,
        "always_include": False,
    },
    "bsc": {
        "chain_id": 56,
        "label": "BNB Chain",
        "rpc_env": "BSC_RPC",
        "rpc_fallbacks": [],
        "rpc_public": "https://bsc.drpc.org",
        "graph_url": None,
        "gas_url": None,
        "gas_key_env": None,
        "always_include": False,
    },
    "avalanche": {
        "chain_id": 43114,
        "label": "Avalanche",
        "rpc_env": "AVAX_RPC",
        "rpc_fallbacks": [],
        "rpc_public": "https://avalanche.drpc.org",
        "graph_url": None,
        "gas_url": None,
        "gas_key_env": None,
        "always_include": False,
    },
}

# Flash loan size for CPMM arbitrage signals: 10 % of the smaller pool's TVL.
# Formula: Flash = 0.10 × min(TVL_pool1, TVL_pool2).
_CPMM_FLASH_SIZE_FRACTION = 0.10

# Minimum pool TVL (USD) for a pool pair to be considered as an arb target.
# Pairs where either pool is shallower than this floor are skipped entirely.
_MIN_POOL_TVL_USD = 1_000.0

# Aave V3 flash-loan fee rate used when computing net profit in signals.
# 5 bps = 0.05% = 0.0005 as specified in the problem statement.
_AAVE_FLASH_FEE_RATE = 0.0005

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
    """State of a single external data feed.

    ``status`` is one of ``"LIVE"``, ``"STALE"`` (last-known-good data
    returned after a failed poll), or ``"FEED ERROR"`` (no data at all).
    """

    name: str
    status: str              # "LIVE", "STALE", or "FEED ERROR"
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
class ChainRpcState:
    """Block number and gas price for a single EVM chain."""

    chain: str          # e.g. "polygon", "ethereum"
    chain_id: int
    label: str
    rpc_url: str        # the URL that was actually used
    status: str         # "LIVE", "STALE", or "FEED ERROR"
    block_number: Optional[int]
    gas_price_gwei: Optional[float]
    fetched_at: float
    latency_ms: float
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": self.chain,
            "chain_id": self.chain_id,
            "label": self.label,
            "rpc_url": self.rpc_url,
            "status": self.status,
            "block_number": self.block_number,
            "gas_price_gwei": self.gas_price_gwei,
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
    flash_size_usd: float       # = 0.10 × min(tvl_buy_usd, tvl_sell_usd)
    cpmm_arb_profit_usd: float  # gross estimated profit at flash_size_usd trade size
    net_profit_usd: float       # gross profit minus 5 bps Aave flash-loan fee


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
    # Primary chain (Polygon) RPC state — kept for backwards compat
    block_number: Optional[int]
    rpc_gas_price_gwei: Optional[float]
    arb_signals: List[ArbitrageSignal]
    all_live: bool
    # Per-chain RPC states keyed by chain slug (e.g. "polygon", "ethereum").
    # Populated for every chain listed by _active_chains().
    chain_states: Dict[str, ChainRpcState] = field(default_factory=dict)
    # Snapshot age: seconds since this data was polled from source
    age_s: float = 0.0
    # True when this snapshot was served from the server-side TTL cache
    from_cache: bool = False


# ---------------------------------------------------------------------------
# Feed poller
# ---------------------------------------------------------------------------


def _resolve_rpc_url(chain_slug: str) -> str:
    """Return the best available RPC URL for a chain using the env rotation.

    Resolution order for Polygon:
      POLYGON_RPC → ALCHEMY_HTTP_1 → ALCHEMY_HTTP_2 → INFURA_HTTP → public

    For other chains, only the chain-specific env var is tried, then the
    public drpc.org fallback.
    """
    cfg = _CHAIN_DEFS.get(chain_slug, {})
    primary = os.getenv(cfg.get("rpc_env", ""), "")
    if primary:
        return primary
    for fb_env in cfg.get("rpc_fallbacks", []):
        v = os.getenv(fb_env, "")
        if v:
            return v
    return cfg.get("rpc_public", "")


def _active_chains() -> List[str]:
    """Return the list of chains that should be polled.

    Polygon is always included.  Additional chains are included when their
    RPC env var is set *or* when ``APEX_CHAINS`` lists them explicitly.

    ``APEX_CHAINS=polygon,ethereum,arbitrum`` overrides auto-detection.
    """
    explicit = os.getenv("APEX_CHAINS", "").strip()
    if explicit:
        return [c.strip().lower() for c in explicit.split(",") if c.strip()]

    chains: List[str] = []
    for slug, cfg in _CHAIN_DEFS.items():
        if cfg.get("always_include"):
            chains.append(slug)
        elif os.getenv(cfg.get("rpc_env", ""), ""):
            chains.append(slug)
    return chains or ["polygon"]


class LiveDataFeeds:
    """Polls live data sources concurrently across all configured chains.

    Parameters
    ----------
    rpc_url:
        Polygon HTTP-RPC endpoint.  Defaults to the best available URL
        resolved from the environment (see ``_resolve_rpc_url``).
    polygonscan_api_key:
        Optional PolygonScan API key (``POLYGONSCAN_API_KEY`` env var).
        Without a key the free unauthenticated endpoint is used; rate-limited
        to 5 req/s.  Absent a key, the feed still works unless the rate limit
        is exceeded.
    graph_url:
        The Graph subgraph endpoint for Polygon.  Override for testing or
        self-hosted Graph nodes.
    timeout_s:
        HTTP timeout for external API calls (default 10 s).

    Caching
    -------
    ``poll_cached(ttl_s)`` returns the last snapshot immediately when it is
    younger than ``ttl_s`` seconds, avoiding redundant external API calls.
    When the underlying feed polls fail, the previous good data is returned
    as ``STALE`` until ``_STALE_FALLBACK_TTL_S`` elapses.
    """

    def __init__(
        self,
        rpc_url: str = "",
        polygonscan_api_key: str = "",
        graph_url: str = _GRAPH_POLYGON_SUBGRAPH,
        timeout_s: float = 10.0,
    ) -> None:
        self._rpc_url = rpc_url or _resolve_rpc_url("polygon")
        self._polygonscan_key = polygonscan_api_key or os.getenv(
            "POLYGONSCAN_API_KEY", ""
        )
        self._graph_url = graph_url
        self._timeout_s = timeout_s
        self._w3 = Web3(
            Web3.HTTPProvider(self._rpc_url, request_kwargs={"timeout": 8})
        )
        self._last_snapshot: Optional[LiveFeedSnapshot] = None

        # Per-feed last-known-good state for stale fallback
        self._last_good_feed: Dict[str, FeedState] = {}
        # Per-chain last-known-good RPC state for stale fallback
        self._last_good_chain: Dict[str, ChainRpcState] = {}

        # Cache support: snapshot + expiry
        self._cached_snapshot: Optional[LiveFeedSnapshot] = None
        self._cache_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def poll_cached(self, ttl_s: float = _CACHE_TTL_S) -> LiveFeedSnapshot:
        """Return the cached snapshot if still fresh; otherwise poll and cache.

        This is the **preferred** call path for the dashboard endpoint.  It
        ensures that external APIs are hit at most once per ``ttl_s`` seconds
        regardless of how frequently the dashboard polls ``/api/feeds``.

        Parameters
        ----------
        ttl_s:
            Cache lifetime in seconds.  Defaults to ``APEX_FEED_CACHE_TTL_S``
            (30 s).  Pass 0 to always force a fresh poll.
        """
        now = time.time()
        if self._cached_snapshot is not None and now < self._cache_expires_at:
            snap = self._cached_snapshot
            snap.age_s = round(now - snap.timestamp, 1)
            snap.from_cache = True
            return snap
        fresh = await self.poll()
        fresh.from_cache = False
        self._cached_snapshot = fresh
        self._cache_expires_at = now + ttl_s
        return fresh

    async def poll(self) -> LiveFeedSnapshot:
        """Poll all feeds concurrently and return an aggregated snapshot.

        Never raises — each individual feed failure is captured as a
        ``FeedState(status="FEED ERROR"|"STALE")`` entry in the returned
        snapshot.  When a feed fails but a recent last-known-good state exists
        (within ``APEX_FEED_STALE_TTL_S``), that state is promoted to
        ``"STALE"`` so the dashboard always has something to show.
        """
        t_start = time.time()
        chains = _active_chains()

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_s)
        ) as session:
            # One CoinGecko call shared across all chains — never duplicated.
            # Per-chain: The Graph + gas oracle + RPC, all in parallel.
            tasks = [
                self._poll_graph(session),
                self._poll_coingecko(session),
                self._poll_etherscan_gas(session),
            ]
            # Kick off per-chain RPC polls (includes Polygon + extras)
            chain_rpc_tasks = [
                self._poll_chain_rpc(chain_slug) for chain_slug in chains
            ]
            results = await asyncio.gather(*(tasks + chain_rpc_tasks))

        graph_state: FeedState = results[0]
        coingecko_state: FeedState = results[1]
        etherscan_state: FeedState = results[2]
        chain_rpc_results: List[ChainRpcState] = list(results[3:])

        # Apply stale-data fallback for named feeds
        graph_state = self._apply_stale_fallback("the_graph", graph_state)
        coingecko_state = self._apply_stale_fallback("coingecko", coingecko_state)
        etherscan_state = self._apply_stale_fallback("etherscan_gas", etherscan_state)

        # Apply stale-data fallback for per-chain RPC states
        chain_states: Dict[str, ChainRpcState] = {}
        for cs in chain_rpc_results:
            cs = self._apply_chain_stale_fallback(cs)
            chain_states[cs.chain] = cs

        feeds: Dict[str, FeedState] = {
            "the_graph": graph_state,
            "coingecko": coingecko_state,
            "etherscan_gas": etherscan_state,
        }
        # Expose Polygon RPC as a named feed for backwards compatibility
        if "polygon" in chain_states:
            pcs = chain_states["polygon"]
            feeds["polygon_rpc"] = FeedState(
                name="polygon_rpc",
                status=pcs.status,
                fetched_at=pcs.fetched_at,
                latency_ms=pcs.latency_ms,
                data={"block_number": pcs.block_number, "gas_price_gwei": pcs.gas_price_gwei},
                error=pcs.error,
            )

        pools = (
            self._parse_graph_pools(graph_state)
            if graph_state.status in ("LIVE", "STALE")
            else []
        )
        token_prices = (
            self._parse_coingecko_prices(coingecko_state)
            if coingecko_state.status in ("LIVE", "STALE")
            else {}
        )
        gas_base, gas_safe, gas_fast = (
            self._parse_etherscan_gas(etherscan_state)
            if etherscan_state.status in ("LIVE", "STALE")
            else (None, None, None)
        )

        # Primary chain (Polygon) for top-level backwards-compat fields
        poly_cs = chain_states.get("polygon")
        block_number: Optional[int] = poly_cs.block_number if poly_cs else None
        rpc_gas_gwei: Optional[float] = poly_cs.gas_price_gwei if poly_cs else None

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
            all_live=all(
                f.status in ("LIVE", "STALE") for f in feeds.values()
            ) and all(
                cs.status in ("LIVE", "STALE") for cs in chain_states.values()
            ),
            chain_states=chain_states,
            age_s=0.0,
        )
        self._last_snapshot = snapshot
        return snapshot

    def last_snapshot(self) -> Optional[LiveFeedSnapshot]:
        """Return the most recently polled snapshot, or None if never polled."""
        return self._last_snapshot

    # ------------------------------------------------------------------
    # Stale-data fallback helpers
    # ------------------------------------------------------------------

    def _apply_stale_fallback(self, key: str, state: FeedState) -> FeedState:
        """Promote a failed feed to STALE using last-known-good data.

        If ``state.status`` is ``"FEED ERROR"`` and a previous LIVE state for
        ``key`` exists within ``_STALE_FALLBACK_TTL_S``, return a copy of that
        state with ``status="STALE"`` and the current error appended.

        Also updates the last-known-good cache when state is LIVE.
        """
        if state.status == "LIVE":
            self._last_good_feed[key] = state
            return state
        last = self._last_good_feed.get(key)
        if last is not None and (time.time() - last.fetched_at) < _STALE_FALLBACK_TTL_S:
            return FeedState(
                name=last.name,
                status="STALE",
                fetched_at=last.fetched_at,
                latency_ms=last.latency_ms,
                data=last.data,
                error=state.error,
            )
        return state

    def _apply_chain_stale_fallback(self, cs: ChainRpcState) -> ChainRpcState:
        """Promote a failed chain RPC state to STALE using last-known-good data."""
        if cs.status == "LIVE":
            self._last_good_chain[cs.chain] = cs
            return cs
        last = self._last_good_chain.get(cs.chain)
        if last is not None and (time.time() - last.fetched_at) < _STALE_FALLBACK_TTL_S:
            return ChainRpcState(
                chain=last.chain,
                chain_id=last.chain_id,
                label=last.label,
                rpc_url=last.rpc_url,
                status="STALE",
                block_number=last.block_number,
                gas_price_gwei=last.gas_price_gwei,
                fetched_at=last.fetched_at,
                latency_ms=last.latency_ms,
                error=cs.error,
            )
        return cs

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

    async def _poll_chain_rpc(self, chain_slug: str) -> ChainRpcState:
        """Fetch block number and gas price for any configured EVM chain.

        Resolves the RPC URL using the env-rotation order defined in
        ``_CHAIN_DEFS`` so every chain uses its best available endpoint.
        Never raises — failures are captured in the returned state.
        """
        cfg = _CHAIN_DEFS.get(chain_slug, {})
        rpc_url = _resolve_rpc_url(chain_slug)
        chain_id = cfg.get("chain_id", 0)
        label = cfg.get("label", chain_slug)
        t0 = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 8}))
            block_number = await loop.run_in_executor(
                None, lambda: w3.eth.block_number
            )
            gas_price_wei = await loop.run_in_executor(
                None, lambda: w3.eth.gas_price
            )
            latency_ms = (time.monotonic() - t0) * 1000.0
            return ChainRpcState(
                chain=chain_slug,
                chain_id=chain_id,
                label=label,
                rpc_url=rpc_url,
                status="LIVE",
                block_number=int(block_number),
                gas_price_gwei=float(gas_price_wei) / 1e9,
                fetched_at=time.time(),
                latency_ms=latency_ms,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            return ChainRpcState(
                chain=chain_slug,
                chain_id=chain_id,
                label=label,
                rpc_url=rpc_url,
                status="FEED ERROR",
                block_number=None,
                gas_price_gwei=None,
                fetched_at=time.time(),
                latency_ms=(time.monotonic() - t0) * 1000.0,
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
           c. Skip pairs where ``min(tvl_buy, tvl_sell) < _MIN_POOL_TVL_USD``
              ($1,000 execution floor).
           d. Set flash size as 10 % of the shallower pool's TVL
              (``flash_size_usd = 0.10 × min(tvl_buy, tvl_sell)``).
           e. Estimate CPMM arbitrage gross profit using the constant-product
              output formula at ``flash_size_usd`` as the input trade size.
           f. Compute net profit by deducting the 5 bps Aave flash-loan fee
              on the flash principal (``net_profit_usd = gross - fee``).
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

                    # Execution floor: skip pairs where either pool is below the
                    # minimum TVL threshold.
                    min_tvl_usd = min(buy_pool.tvl_usd, sell_pool.tvl_usd)
                    if min_tvl_usd < _MIN_POOL_TVL_USD:
                        continue

                    # Dynamic flash-loan sizing: 10 % of the shallower pool's TVL.
                    # Formula: Flash = 0.10 × min(TVL_pool1, TVL_pool2)
                    flash_size_usd = min_tvl_usd * _CPMM_FLASH_SIZE_FRACTION
                    amount_in_t0 = flash_size_usd / p0_usd

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

                    # Net profit = gross profit minus 5 bps Aave flash-loan fee on
                    # the principal borrowed.
                    aave_fee_usd = flash_size_usd * _AAVE_FLASH_FEE_RATE
                    net_profit_usd = arb_profit_usd - aave_fee_usd

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
                            flash_size_usd=round(flash_size_usd, 2),
                            cpmm_arb_profit_usd=round(arb_profit_usd, 6),
                            net_profit_usd=round(net_profit_usd, 6),
                        )
                    )

        signals.sort(key=lambda s: s.spread_bps, reverse=True)
        return signals[:50]
