"""Live token universe discovery for Polygon mainnet.

All data is fetched from live sources in priority order:

1. **The Graph** — Uniswap V3 Polygon subgraph (top pools by TVL).
2. **The Graph** — QuickSwap V3 / Algebra Polygon subgraph (fallback).
3. **CoinGecko free API** — bulk token USD prices by contract address.
4. **On-chain ERC-20 calls** — ``symbol()`` / ``decimals()`` for any
   token whose metadata is missing from the subgraph response.

``_SEED_TOKENS`` is a well-known static registry used ONLY as a final
fallback when all network sources are unreachable.  It must never be the
primary data source in production execution paths.

Typical usage
-------------
::

    from apex_omega_core.core.token_universe import (
        fetch_token_universe,
        fetch_coingecko_prices,
    )

    tokens = fetch_token_universe(rpc_url, max_tokens=20)
    # tokens: Dict[str, Tuple[str, int]]  →  symbol → (checksummed_address, decimals)

    prices = fetch_coingecko_prices(list(tokens.values()))
    # prices: Dict[str, float]  →  checksummed_address_lower → usd_price
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests
from web3 import Web3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol constants (immutable – not "data")
# ---------------------------------------------------------------------------

# Minimum USD TVL for a pool to contribute a token to the universe.
_MIN_POOL_TVL_USD: float = 50_000.0

# The Graph hosted-service endpoints for Polygon
_GRAPH_UNISWAP_V3_POLYGON = (
    "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon"
)
_GRAPH_QUICKSWAP_V3_POLYGON = (
    "https://api.thegraph.com/subgraphs/name/sameepsi/quickswap-v3"
)

# CoinGecko free API – no API key required for price lookups by address
_COINGECKO_TOKEN_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/token_price/polygon-pos"
)

# Minimal ERC-20 ABI (decimals + symbol only)
_ERC20_META_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ---------------------------------------------------------------------------
# Seed registry — FALLBACK ONLY when all network sources are unreachable
# ---------------------------------------------------------------------------
# These values are correct as of Polygon mainnet April 2025.
# They are intentionally NOT used as the primary source in any execution path.

_SEED_TOKENS: Dict[str, Tuple[str, int]] = {
    # Stablecoins
    "USDCe":  ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "USDC":   ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    "USDT":   ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":    ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    # Majors
    "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH":   ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC":   ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    # Blue-chip DeFi
    "LINK":   ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
    "AAVE":   ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "CRV":    ("0x172370d5Cd63279eFa6d502DAB29171933a610AF", 18),
    "BAL":    ("0x9a71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3", 18),
    "SUSHI":  ("0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a", 18),
    "UNI":    ("0xb33EaAd8d922B1083446DC23f610c2567fB5180f", 18),
    "QUICK":  ("0xB5C064F955D8e7F38fE0460C556a72987494eE17", 18),
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_GRAPH_QUERY = """
{
  pools(
    first: 200
    orderBy: totalValueLockedUSD
    orderDirection: desc
    where: { totalValueLockedUSD_gt: "%s" }
  ) {
    token0 { id symbol decimals }
    token1 { id symbol decimals }
    totalValueLockedUSD
  }
}
""" % int(_MIN_POOL_TVL_USD)


def _query_graph(endpoint: str, timeout: float = 8.0) -> List[dict]:
    """Execute a The Graph pool query and return the pool list.

    Returns an empty list on any error so that callers can fall through
    to the next source without raising.
    """
    try:
        resp = requests.post(
            endpoint,
            json={"query": _GRAPH_QUERY},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("pools", [])
    except Exception as exc:
        logger.debug("The Graph query failed (%s): %s", endpoint, exc)
        return []


def _pools_to_token_tvl(pools: List[dict]) -> Dict[str, dict]:
    """Aggregate per-token TVL from a list of pool dicts (from The Graph).

    Returns ``{address_lower: {"symbol": str, "decimals": int, "tvl": float}}``.
    Tokens whose symbol or decimals are missing are skipped (they will
    be resolved later via on-chain ERC-20 calls if needed).
    """
    token_tvl: Dict[str, dict] = {}
    for pool in pools:
        try:
            tvl = float(pool.get("totalValueLockedUSD", 0.0))
            for side in ("token0", "token1"):
                tok = pool[side]
                addr = tok["id"].lower()
                sym = tok.get("symbol", "").strip()
                dec_raw = tok.get("decimals")
                if not sym or dec_raw is None:
                    continue
                try:
                    dec = int(dec_raw)
                except (ValueError, TypeError):
                    continue
                if addr not in token_tvl:
                    token_tvl[addr] = {"symbol": sym, "decimals": dec, "tvl": 0.0}
                token_tvl[addr]["tvl"] += tvl
        except (KeyError, TypeError, ValueError):
            continue
    return token_tvl


def _resolve_erc20_metadata(
    w3: Web3,
    addresses: List[str],
    timeout_per_call: float = 3.0,
) -> Dict[str, Tuple[str, int]]:
    """On-chain fallback: call ``symbol()`` and ``decimals()`` for each address.

    Returns ``{address_lower: (symbol, decimals)}``.  Addresses that fail
    (non-ERC20 or call reverts) are silently omitted.
    """
    out: Dict[str, Tuple[str, int]] = {}
    for addr in addresses:
        try:
            csum = Web3.to_checksum_address(addr)
            contract = w3.eth.contract(address=csum, abi=_ERC20_META_ABI)
            symbol = contract.functions.symbol().call()
            decimals = contract.functions.decimals().call()
            if symbol and isinstance(decimals, int):
                out[addr.lower()] = (symbol, int(decimals))
        except Exception as exc:
            logger.debug("ERC-20 metadata call failed (%s): %s", addr, exc)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_token_universe(
    rpc_url: Optional[str] = None,
    w3: Optional[Web3] = None,
    max_tokens: int = 20,
    min_tvl_usd: float = _MIN_POOL_TVL_USD,
    timeout: float = 8.0,
) -> Dict[str, Tuple[str, int]]:
    """Fetch the live top-N token universe for Polygon mainnet.

    Discovery pipeline (in priority order):

    1. The Graph — Uniswap V3 Polygon subgraph (most comprehensive).
    2. The Graph — QuickSwap V3 Polygon subgraph (if step 1 returns < 5 tokens).
    3. On-chain ERC-20 calls — ``symbol()`` / ``decimals()`` for tokens
       whose metadata is missing from the subgraph.
    4. ``_SEED_TOKENS`` — static fallback used only when all live sources fail.

    Parameters
    ----------
    rpc_url:
        Polygon RPC endpoint.  Used only when on-chain ERC-20 fallback is needed.
        If omitted, the ``POLYGON_RPC`` environment variable is used.
    w3:
        Pre-built ``Web3`` instance.  Preferred over ``rpc_url`` when already
        connected (avoids re-connecting).
    max_tokens:
        Maximum number of tokens to include.  Tokens are ranked by cumulative
        pool TVL; the top ``max_tokens`` are returned.
    min_tvl_usd:
        Minimum pool TVL to include a token.  Pools below this threshold are
        excluded from the TVL ranking.
    timeout:
        HTTP request timeout in seconds for subgraph queries.

    Returns
    -------
    Dict[str, Tuple[str, int]]
        ``{symbol: (checksummed_address, decimals)}`` for the top tokens
        ranked by on-chain pool TVL.

    Raises
    ------
    RuntimeError
        If all discovery sources fail AND no ``w3`` / ``rpc_url`` is available
        to fall back to on-chain ERC-20 resolution.  In practice, ``_SEED_TOKENS``
        prevents this from ever raising.
    """
    raw: Dict[str, dict] = {}

    # ── Step 1: Uniswap V3 Polygon subgraph ────────────────────────────────
    pools = _query_graph(_GRAPH_UNISWAP_V3_POLYGON, timeout=timeout)
    if pools:
        raw = _pools_to_token_tvl(pools)
        logger.debug("token_universe: %d tokens from UniV3 subgraph", len(raw))

    # ── Step 2: QuickSwap V3 fallback if Step 1 was thin ───────────────────
    if len(raw) < 5:
        qs_pools = _query_graph(_GRAPH_QUICKSWAP_V3_POLYGON, timeout=timeout)
        if qs_pools:
            qs_raw = _pools_to_token_tvl(qs_pools)
            for addr, info in qs_raw.items():
                if addr not in raw:
                    raw[addr] = info
                else:
                    raw[addr]["tvl"] += info["tvl"]
            logger.debug(
                "token_universe: %d tokens after QuickSwap V3 merge", len(raw)
            )

    # ── Step 3: resolve missing decimals on-chain ───────────────────────────
    missing = [a for a, info in raw.items() if info.get("decimals") is None]
    if missing:
        _w3 = w3
        if _w3 is None and rpc_url:
            _w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
        if _w3 is not None:
            resolved = _resolve_erc20_metadata(_w3, missing)
            for addr, (sym, dec) in resolved.items():
                if addr in raw:
                    raw[addr].update({"symbol": sym, "decimals": dec})

    # ── Step 4: fallback if The Graph returned nothing ─────────────────────
    if not raw:
        logger.warning(
            "token_universe: all live sources failed — using _SEED_TOKENS fallback"
        )
        return dict(list(_SEED_TOKENS.items())[:max_tokens])

    # ── Rank by TVL, take top max_tokens, return as {symbol: (address, dec)} ─
    ranked = sorted(raw.items(), key=lambda kv: kv[1]["tvl"], reverse=True)
    result: Dict[str, Tuple[str, int]] = {}
    seen_symbols: set = set()
    for addr_lower, info in ranked:
        if len(result) >= max_tokens:
            break
        sym = info.get("symbol", "")
        dec = info.get("decimals")
        if not sym or dec is None:
            continue
        # Deduplicate by symbol (some tokens have multiple bridged variants
        # with different symbols – keep the first/highest-TVL occurrence)
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        try:
            csum_addr = Web3.to_checksum_address(addr_lower)
        except Exception:
            continue
        result[sym] = (csum_addr, int(dec))

    if not result:
        logger.warning("token_universe: ranked result empty — using _SEED_TOKENS")
        return dict(list(_SEED_TOKENS.items())[:max_tokens])

    logger.info(
        "token_universe: resolved %d live tokens (max_tokens=%d)",
        len(result), max_tokens,
    )

    # ── Apply env overlay: APEX_TOKEN_<SYMBOL>=<address>:<decimals> ───────
    result = _apply_env_overlay(result)

    return result


def _apply_env_overlay(
    universe: Dict[str, Tuple[str, int]],
) -> Dict[str, Tuple[str, int]]:
    """Apply operator-specified token overrides from environment variables.

    Any ``APEX_TOKEN_<SYMBOL>=<address>:<decimals>`` env var pins that token
    to the given address and decimals, overriding or adding to the live
    universe.  This is the only supported mechanism for injecting custom tokens
    without changing source code.

    Example::

        APEX_TOKEN_MYTOKEN=0xABC...123:18

    Malformed entries (wrong format, invalid address, non-integer decimals) are
    logged and skipped — they never break the scan.
    """
    prefix = "APEX_TOKEN_"
    result = dict(universe)
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        sym = key[len(prefix):]
        if not sym:
            continue
        parts = val.strip().split(":")
        if len(parts) != 2:
            logger.warning(
                "token_universe env overlay: malformed %s=%r (expected addr:decimals)",
                key, val,
            )
            continue
        raw_addr, raw_dec = parts
        try:
            csum_addr = Web3.to_checksum_address(raw_addr.strip())
            dec = int(raw_dec.strip())
        except Exception as exc:
            logger.warning(
                "token_universe env overlay: invalid %s=%r — %s", key, val, exc
            )
            continue
        result[sym] = (csum_addr, dec)
        logger.info(
            "token_universe env overlay: pinned %s → %s (decimals=%d)",
            sym, csum_addr[:12] + "…", dec,
        )
    return result


def fetch_coingecko_prices(
    tokens: Dict[str, Tuple[str, int]],
    timeout: float = 6.0,
) -> Dict[str, float]:
    """Fetch live USD prices for a token set from CoinGecko (no API key).

    Parameters
    ----------
    tokens:
        ``{symbol: (address, decimals)}`` dict as returned by
        :func:`fetch_token_universe`.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    Dict[str, float]
        ``{symbol: usd_price}``.  Tokens whose price is unavailable are
        omitted; the caller should fill gaps (e.g. 1.0 for stablecoins).
    """
    if not tokens:
        return {}

    addr_to_sym: Dict[str, str] = {
        addr.lower(): sym for sym, (addr, _) in tokens.items()
    }
    contract_addresses = ",".join(addr_to_sym.keys())

    try:
        resp = requests.get(
            _COINGECKO_TOKEN_PRICE_URL,
            params={
                "contract_addresses": contract_addresses,
                "vs_currencies": "usd",
            },
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("CoinGecko price fetch failed: %s", exc)
        return {}

    prices: Dict[str, float] = {}
    for addr_lower, price_data in data.items():
        sym = addr_to_sym.get(addr_lower.lower())
        if sym and "usd" in price_data:
            try:
                prices[sym] = float(price_data["usd"])
            except (ValueError, TypeError):
                pass

    logger.debug(
        "coingecko_prices: fetched %d/%d prices", len(prices), len(tokens)
    )
    return prices


def build_token_price_map(
    tokens: Dict[str, Tuple[str, int]],
    pool_derived: Optional[Dict[str, float]] = None,
    coingecko_timeout: float = 6.0,
) -> Dict[str, float]:
    """Produce a complete token→USD price map using all available sources.

    Priority:
    1. Pool-derived prices (CPMM ratio from live reserves) — most accurate
       for AMM pricing at current block.
    2. CoinGecko prices — fills gaps for tokens without USDC/USDT pool legs.
    3. Stablecoin peg (1.0) for known stables.
    4. 0.0 sentinel for any token still unpriced (excluded from arb scoring).

    Parameters
    ----------
    tokens:
        Live token universe from :func:`fetch_token_universe`.
    pool_derived:
        Optional price map already computed from pool reserves (e.g. by
        ``_derive_token_prices_usd`` in dry_run.py).  These take precedence.
    coingecko_timeout:
        HTTP timeout for the CoinGecko fallback call.
    """
    stables = {"USDC", "USDCe", "USDT", "DAI", "FRAX", "TUSD", "MAI", "BUSD"}

    prices: Dict[str, float] = {}

    # Seed stablecoins at $1.00
    for sym in tokens:
        if sym in stables:
            prices[sym] = 1.0

    # Layer pool-derived prices (override stablecoin peg if a real pool says different)
    if pool_derived:
        prices.update(pool_derived)

    # CoinGecko fills any remaining gaps
    missing_syms = [s for s in tokens if s not in prices]
    if missing_syms:
        missing_subset = {s: tokens[s] for s in missing_syms}
        cg = fetch_coingecko_prices(missing_subset, timeout=coingecko_timeout)
        prices.update(cg)

    return prices
