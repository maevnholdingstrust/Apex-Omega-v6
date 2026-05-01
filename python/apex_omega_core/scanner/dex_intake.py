"""dex_intake.py    production-ready discovery scaffold

  NO mock data
  NO pre-filled prices / reserves
  Every external fetch has a clearly-named hook that must be
   implemented by your infra layer (GraphQL, RPC batch, REST, cache).
  Safe defaults: returns an empty list if no data provider is wired.
"""

from __future__ import annotations
import os
import time
from typing import List, Dict, Callable, Any

# ----------------------------------------------------------------------
# Type helpers
# ----------------------------------------------------------------------

RawOpportunity = Dict[str, Any]      # for clarity

# ----------------------------------------------------------------------
# Provider hooks (MUST be patched by your runtime)
# ----------------------------------------------------------------------

#: callable(): List[Dict]    must return token meta (address, symbol, decimals)
TOKEN_UNIVERSE_PROVIDER: Callable[[], List[Dict]] | None = None

#: callable([...tokens...]) -> List[Dict]
#: returns dex-specific pool json (liquidity, fee tier, reserves, tick, etc.)
POOL_STATE_PROVIDER: Callable[[List[Dict]], List[Dict]] | None = None

#: optional on-chain price oracle / dex-screener fallback
SPOT_PRICE_PROVIDER: Callable[[str], float] | None = None

#: simple in-mem or redis cache (inject at runtime)
CACHE_GET: Callable[[str], Any] | None = None
CACHE_SET: Callable[[str, Any, int], None] | None = None   # key, obj, ttl-sec


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
MAX_TOKENS     = int(os.getenv("MAX_TOKENS_PER_SCAN", "100"))
CACHE_TTL_SEC  = int(os.getenv("DEX_INT_CACHE_TTL_SEC", "900"))
MIN_LIQ_USD    = float(os.getenv("DISCOVERY_MIN_LIQ_USD", "10000"))   # ignore dust

# ----------------------------------------------------------------------
# Core helpers
# ----------------------------------------------------------------------

def _cache_get(key: str) -> Any | None:
    return CACHE_GET(key) if CACHE_GET else None


def _cache_set(key: str, obj: Any, ttl: int = CACHE_TTL_SEC) -> None:
    if CACHE_SET:
        CACHE_SET(key, obj, ttl)


# ----------------------------------------------------------------------
# Discovery logic
# ----------------------------------------------------------------------

def _fetch_token_universe() -> List[Dict]:
    if TOKEN_UNIVERSE_PROVIDER is None:
        return []
    cache_key = "token_universe"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    tokens = TOKEN_UNIVERSE_PROVIDER()[:MAX_TOKENS]
    _cache_set(cache_key, tokens)
    return tokens


def _fetch_pool_states(tokens: List[Dict]) -> List[Dict]:
    if POOL_STATE_PROVIDER is None or not tokens:
        return []
    cache_key = f"pool_state_{len(tokens)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    pools = POOL_STATE_PROVIDER(tokens)
    _cache_set(cache_key, pools)
    return pools


def _best_price(token: str) -> float | None:
    if SPOT_PRICE_PROVIDER is None:
        return None
    cache_key = f"spot_price_{token}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    price = SPOT_PRICE_PROVIDER(token)
    if price is not None:
        _cache_set(cache_key, price, ttl=60)
    return price


def _build_raw_op(pool: Dict) -> RawOpportunity | None:
    """Transform a pool-state blob into the raw-opportunity schema."""
    liqu_usd = pool.get("tvl_usd")
    if liqu_usd is None or liqu_usd < MIN_LIQ_USD:
        return None

    token0, token1 = pool["token0_symbol"], pool["token1_symbol"]
    pair = f"{token0}/{token1}"

    price_hint = _best_price(token0)
    now = int(time.time())

    return {
        "pair": pair,
        "best_buy_venue": pool["buy_venue"],
        "best_sell_venue": pool["sell_venue"],
        "reserve0": pool["reserve0"],
        "reserve1": pool["reserve1"],
        "fee": pool["fee_decimal"],
        "raw_spread_bps": pool["spread_bps"],
        "leg1_tvl_usd": liqu_usd,
        "tvl_usd": liqu_usd,
        "timestamp": now,
        "price_hint": price_hint,
        # baseline fields required by hard-gate layer
        "rpc_healthy": pool.get("rpc_ok", True),
        "reserves_verified": pool.get("reserve_verified", True),
        "reserve_staleness_seconds": pool.get("reserve_age_sec", 0),
        "pool_type": pool["pool_type"],          # "V2" | "V3" | "ALGEBRA"
        "route_calldata": b"",                   # filled later by route builder
    }


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def discover_pools(max_tokens: int | None = None) -> List[RawOpportunity]:
    """
    Returns a list of raw opportunity dicts.
    Each dict is free of pre-filled profit or sizing; downstream logic
    determines those values every scan cycle.
    """
    if max_tokens is not None:
        globals()["MAX_TOKENS"] = max_tokens

    tokens = _fetch_token_universe()
    pools  = _fetch_pool_states(tokens)

    result: List[RawOpportunity] = []
    for p in pools:
        op = _build_raw_op(p)
        if op:
            result.append(op)
    return result
