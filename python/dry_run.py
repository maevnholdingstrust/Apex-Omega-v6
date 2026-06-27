#!/usr/bin/env python3
"""
Dry run script for Apex-Omega-v6 Polygon arbitrage system.
Exercises core components and measures performance.

Live scan mode queries real Polygon on-chain data and records
expected_net_edge, p_fill, and E[profit] for 100 opportunities.
"""

import asyncio
import copy
import csv
import functools
import itertools
import json
import math
import os
import random as _random
import time
import logging
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from web3 import Web3

from apex_omega_core.core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.deterministic_slippage import calculate_deterministic_slippage_bps
from apex_omega_core.core.inference import derive_net_edge
from apex_omega_core.core.feature_factory import extract_features
from apex_omega_core.strategies.execution_router import ExecutionRouter
from apex_omega_core.operations.validate_spread_alignment import validate_spread_alignment
from apex_omega_core.core.domain_types import Spread, ArbitrageOpportunity, Pool, FlashLoanConfig
from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor, ArbitrageDetector
from apex_omega_core.core.mev_gas_oracle import (
    GasOracle, TipOptimizer,
)
from apex_omega_core.core.expanded_graph_scan import (
    expanded_graph_scan, ExpandedGraphScanResult,
)
from apex_omega_core.core.expanded_strategy_steps import (
    _algebra_live_amount_out,
    _balancer_live_amount_out,
    _curve_live_amount_out,
    _pool_id_bytes,
    _v2_live_amount_out,
    _v3_live_amount_out,
    _venue_and_fee,
)
from apex_omega_core.core.polygon_market_registry import TOKENS, VENUES
from apex_omega_core.core.rpc_rotation import RpcRotationManager, RpcRotationError, collect_rpc_urls
from apex_omega_core.core.route_graph import RouteGraph
from apex_omega_core.core.v2_cpmm_math import two_pool_cpmm_optimal_input

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Live scan: on-chain ABIs
# ---------------------------------------------------------------------------

_UNIV3_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_UNIV3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_QSV2_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_QSV2_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ---------------------------------------------------------------------------
# Live scan: token / DEX registry
# ---------------------------------------------------------------------------

# (checksummed address, decimals).  Polygon mainnet token registry.
# Anything that doesn't have â‰¥2 surviving pools after the liquidity
# filter is dropped automatically â€” extra entries cost nothing.
_TOKENS: Dict[str, Tuple[str, int]] = {
    # Stablecoins
    "USDCe":  ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),   # bridged USDC
    "USDC":   ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),   # native USDC
    "USDT":   ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":    ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "FRAX":   ("0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89", 18),
    "MAI":    ("0xa3Fa99A148fA48D14Ed51d610c367C61876997F1", 18),
    "TUSD":   ("0x2e1AD108fF1D8C782fcBbB89AAd783aC49586756", 18),
    # Majors / wrapped
    "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH":   ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC":   ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    # MATIC LSDs
    "stMATIC":("0x3A58a54C066FdC0f2D55FC9C89F0415C92eBf3C4", 18),
    "MaticX": ("0xfa68FB4628DFF1028CFEc22b4162FCcd0d45efb6", 18),
    # ETH LSDs
    "wstETH": ("0x03b54A6e9a984069379fae1a4fC4dBAE93B3bCCD", 18),
    # Blue-chip DeFi
    "LINK":   ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
    "AAVE":   ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "CRV":    ("0x172370d5Cd63279eFa6d502DAB29171933a610AF", 18),
    "BAL":    ("0x9a71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3", 18),
    "SUSHI":  ("0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a", 18),
    "UNI":    ("0xb33EaAd8d922B1083446DC23f610c2567fB5180f", 18),
    "COMP":   ("0x8505b9d2254A7Ae468c0E9dd10Ccea3A837aef5c", 18),
    "MKR":    ("0x6f7C932e7684666C9fd1d44527765433e01fF61d", 18),
    "SNX":    ("0x50B728D8D964fd00C2d0AAD81718b71311feF68a", 18),
    "GHST":   ("0x385Eeac5cB85A38A9a07A70c73e0a3271CfB54A7", 18),
    "QUICK":  ("0xB5C064F955D8e7F38fE0460C556a72987494eE17", 18),
    "FXS":    ("0x1a3acf6D19267E2d3e7f898f42803e90C9219062", 18),
    "DPI":    ("0x85955046DF4668e1DD369D2DE9f3AEFC9cD8DA0E", 18),
    # Gaming / metaverse
    "SAND":   ("0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683", 18),
    "MANA":   ("0xA1c57f48F0Deb89f569dFbE6E2B7f46D33606fD4", 18),
    # Balancer-heavy / expanded Polygon universe
    "TEL":    ("0xdf7837de1F2Fa4631D716CF2502f8b230F1dcc32", 2),
    "APE":    ("0xB7b31a6BC18e48888545CE79E83E06003be70930", 18),
    "OLAS":   ("0xfEf5d947472e72Efbb2E388c730B7428406F2F95", 18),
    "TETU":   ("0x255707B70BF90aa112006E1b07B9AeA6De021424", 18),
    "GRT":    ("0x5fe2B58c013d7601147DcdD68C143A77499f5531", 18),
    "VISION": ("0x034b2090b579228482520c589dbD397c53FC51cC", 18),
}


def _load_extra_tokens_from_env() -> None:
    """Extend token universe from EXTRA_POLYGON_TOKENS.

    Format:
      SYMBOL=0xAddress:decimals,SYMBOL2=0xAddress:decimals
    """
    raw = os.getenv("EXTRA_POLYGON_TOKENS", "").strip()
    if not raw:
        return
    for item in raw.split(","):
        part = item.strip()
        if not part or "=" not in part or ":" not in part:
            continue
        symbol, rest = part.split("=", 1)
        address, decimals = rest.rsplit(":", 1)
        symbol = symbol.strip()
        try:
            checksum = Web3.to_checksum_address(address.strip())
            dec = int(decimals.strip())
        except Exception:
            logger.warning("Ignoring invalid EXTRA_POLYGON_TOKENS entry: %s", part)
            continue
        if symbol and 0 <= dec <= 36:
            _TOKENS[symbol] = (checksum, dec)


_load_extra_tokens_from_env()

# Auto-generate ALL unordered pair combinations from the token
# registry.  No hand-curated whitelist â€” let the filter layer decide
# which pools are real.  C(8,2) = 28 pairs.
_PAIRS: List[Tuple[str, str]] = [
    (a, b)
    for i, a in enumerate(sorted(_TOKENS))
    for b in sorted(_TOKENS)[i + 1:]
]

_UNIV3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
_QSV2_FACTORY  = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"

# Balancer V2 vault (same address on every chain).
_BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

# Whitelist of well-known Balancer V2 50/50 weighted pools on Polygon.
# Format: (poolId, fee_decimal).  Pools that don't exist on-chain or
# whose token set isn't in ``_TOKENS`` are silently dropped at scan
# time.  Add real pool IDs here as they're verified â€” the discovery
# pipeline tolerates an empty list.
_BALANCER_W50_POOLS: List[Tuple[str, float]] = []


def _load_balancer_weighted_pools_from_env() -> None:
    """Extend Balancer weighted pool discovery from BALANCER_W50_POOL_IDS.

    Format:
      0xPoolId:0.003,0xPoolId2:0.001
    """
    raw = os.getenv("BALANCER_W50_POOL_IDS", "").strip()
    if not raw:
        return
    for item in raw.split(","):
        part = item.strip()
        if not part:
            continue
        if ":" in part:
            pool_id, fee_raw = part.rsplit(":", 1)
        else:
            pool_id, fee_raw = part, "0.003"
        pool_id = pool_id.strip()
        try:
            if not pool_id.startswith("0x") or len(Web3.to_bytes(hexstr=pool_id)) != 32:
                raise ValueError("poolId must be bytes32 hex")
            fee = float(fee_raw)
        except Exception:
            logger.warning("Ignoring invalid BALANCER_W50_POOL_IDS entry: %s", part)
            continue
        _BALANCER_W50_POOLS.append((pool_id, fee))


_load_balancer_weighted_pools_from_env()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _leg_price_invariant(
    *,
    leg_amounts_in: List[float],
    leg_amounts_out: List[float],
    quote_block: int = 0,
) -> Dict[str, Any]:
    """Route-level LEG1/LEG2 executable price telemetry.

    LEG1 is the entry swap A -> B. LEG2 is the complete exit segment beginning
    with B and ending back in A. For 2-hop routes this is the second swap; for
    3/4-hop routes it is the chained remainder of the route.

    The price comparison is diagnostic only. Execution eligibility is decided
    by chained final output after flash-loan fee, gas value, and owner surplus.
    """
    result: Dict[str, Any] = {
        "buy_leg1_price": 0.0,
        "sell_leg2_price": 0.0,
        "leg_price_executable_spread_abs": 0.0,
        "leg_price_executable_spread_bps": 0.0,
        "leg_price_invariant_status": "NO_EXECUTABLE_PRICE_EDGE",
        "leg_price_invariant_reason": "",
        "leg_price_quote_block": int(quote_block or 0),
    }
    if len(leg_amounts_in) < 2 or len(leg_amounts_out) < 2:
        result["leg_price_invariant_reason"] = "LEG_PRICE_AMOUNTS_MISSING"
        return result

    leg1_amount_in_a = float(leg_amounts_in[0])
    leg1_amount_out_b = float(leg_amounts_out[0])
    leg2_amount_in_b = float(leg_amounts_in[1])
    leg2_amount_out_a = float(leg_amounts_out[-1])

    if leg1_amount_in_a <= 0:
        result["leg_price_invariant_reason"] = "LEG1_AMOUNT_IN_A_INVALID"
        return result
    if leg1_amount_out_b <= 0:
        result["leg_price_invariant_reason"] = "LEG1_AMOUNT_OUT_B_INVALID"
        return result
    if leg2_amount_in_b <= 0:
        result["leg_price_invariant_reason"] = "LEG2_AMOUNT_IN_B_INVALID"
        return result
    if leg2_amount_out_a <= 0:
        result["leg_price_invariant_reason"] = "LEG2_AMOUNT_OUT_A_INVALID"
        return result
    if leg2_amount_in_b > leg1_amount_out_b:
        result["leg_price_invariant_reason"] = "LEG2_INPUT_EXCEEDS_LEG1_EXECUTABLE_OUTPUT"
        return result

    buy_leg1_price = leg1_amount_in_a / leg1_amount_out_b
    sell_leg2_price = leg2_amount_out_a / leg2_amount_in_b
    spread_abs = sell_leg2_price - buy_leg1_price
    spread_bps = (spread_abs / buy_leg1_price) * 10_000.0 if buy_leg1_price > 0 else 0.0
    result.update(
        {
            "buy_leg1_price": round(buy_leg1_price, 18),
            "sell_leg2_price": round(sell_leg2_price, 18),
            "leg_price_executable_spread_abs": round(spread_abs, 18),
            "leg_price_executable_spread_bps": round(spread_bps, 8),
        }
    )
    if buy_leg1_price <= 0:
        result["leg_price_invariant_reason"] = "BUY_LEG1_PRICE_INVALID"
        return result
    if sell_leg2_price <= 0:
        result["leg_price_invariant_reason"] = "SELL_LEG2_PRICE_INVALID"
        return result
    if buy_leg1_price >= sell_leg2_price:
        result["leg_price_invariant_status"] = "LEG_PRICE_EDGE_INVERTED_DIAGNOSTIC"
        result["leg_price_invariant_reason"] = "BUY_LEG1_PRICE_NOT_BELOW_SELL_LEG2_PRICE"
        return result

    result["leg_price_invariant_status"] = "LEG_PRICE_EDGE_VALID"
    result["leg_price_invariant_reason"] = "BUY_LEG1_PRICE_LT_SELL_LEG2_PRICE"
    return result


def _leg_price_has_structural_failure(leg_price: Dict[str, Any]) -> bool:
    """Return True only for impossible quote chains, not negative price edge telemetry."""
    return leg_price.get("leg_price_invariant_reason") in {
        "LEG_PRICE_AMOUNTS_MISSING",
        "LEG1_AMOUNT_IN_A_INVALID",
        "LEG1_AMOUNT_OUT_B_INVALID",
        "LEG2_AMOUNT_IN_B_INVALID",
        "LEG2_AMOUNT_OUT_A_INVALID",
        "LEG2_INPUT_EXCEEDS_LEG1_EXECUTABLE_OUTPUT",
    }


def _fetch_balancer_api_weighted_pairs() -> List["_PoolSnapshot"]:
    """Discover Polygon Balancer weighted pools from the Balancer API.

    The API is discovery-only here.  Balancer routes are marked with a
    non-CPMM kind so deterministic reserve math cannot create fake positives;
    executable scoring must come from the Balancer live quote adapter.
    """
    if not _env_bool("BALANCER_API_DISCOVERY_ENABLED", True):
        return []
    api_url = os.getenv("BALANCER_API_URL", "https://api-v3.balancer.fi/").strip()
    first = max(1, min(500, int(float(os.getenv("BALANCER_API_FIRST", "100")))))
    min_tvl = max(
        5_000.0,
        float(os.getenv("BALANCER_API_MIN_TVL_USD", os.getenv("MIN_POOL_TVL_USD", "5000"))),
    )
    query = """
    query($first:Int,$minTvl:Float){
      poolGetPools(
        first:$first,
        orderBy: totalLiquidity,
        orderDirection: desc,
        where:{
          chainIn:[POLYGON],
          minTvl:$minTvl,
          poolTypeIn:[WEIGHTED],
          protocolVersionIn:[2]
        }
      ) {
        id
        address
        dynamicData {
          poolId
          swapFee
          totalLiquidity
          swapEnabled
          isPaused
          isInRecoveryMode
        }
        poolTokens {
          address
          decimals
          balance
          weight
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"first": first, "minTvl": min_tvl}}).encode()
    req = Request(
        api_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Apex-Omega-v6/1.0 BalancerDiscovery",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=float(os.getenv("BALANCER_API_TIMEOUT_SEC", "12"))) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
    except (OSError, URLError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Balancer API discovery failed: %s", exc)
        return []
    pools = ((data.get("data") or {}).get("poolGetPools") or [])
    max_tokens_per_pool = max(2, min(8, int(float(os.getenv("BALANCER_MAX_TOKENS_PER_POOL", "5")))))
    out: List[_PoolSnapshot] = []
    for pool in pools:
        dyn = pool.get("dynamicData") or {}
        if not dyn.get("swapEnabled") or dyn.get("isPaused") or dyn.get("isInRecoveryMode"):
            continue
        pool_id = str(dyn.get("poolId") or pool.get("id") or "").strip()
        try:
            if not pool_id.startswith("0x") or len(Web3.to_bytes(hexstr=pool_id)) != 32:
                continue
            if float(dyn.get("totalLiquidity") or 0.0) < min_tvl:
                continue
            fee = float(dyn.get("swapFee") or 0.0)
        except Exception:
            continue
        tokens = pool.get("poolTokens") or []
        if len(tokens) < 2 or len(tokens) > max_tokens_per_pool:
            continue
        token_rows = []
        for tok in tokens:
            sym = _addr_to_sym(str(tok.get("address") or ""))
            if sym is None:
                continue
            try:
                bal = float(tok.get("balance") or 0.0)
                weight = float(tok.get("weight") or 0.0)
            except Exception:
                continue
            if bal <= 0.0 or weight <= 0.0:
                continue
            token_rows.append((sym, str(tok.get("address") or ""), bal, weight))
        if len(token_rows) < 2:
            continue
        pool_address = Web3.to_checksum_address(str(pool.get("address") or ("0x" + pool_id[2:42])))
        for left, right in itertools.combinations(token_rows, 2):
            rows = [left, right]
            if rows[0][1].lower() > rows[1][1].lower():
                rows = list(reversed(rows))
            sym0, _addr0, bal0, weight0 = rows[0]
            sym1, _addr1, bal1, weight1 = rows[1]
            # Weighted-pool marginal token1-per-token0 price. This is only
            # used for price graph evidence; execution uses queryBatchSwap.
            price = (bal1 / weight1) / (bal0 / weight0)
            out.append(_PoolSnapshot(
                pool_address=pool_address,
                dex="balancer_weighted",
                fee=fee,
                sym0=sym0,
                sym1=sym1,
                reserve0=bal0,
                reserve1=bal1,
                price=price,
                kind="balancer_weighted",
                pool_id=pool_id,
            ))
    return out

# Curve am3CRV 3-coin StableSwap pool on Polygon (DAI / USDCe / USDT).
_CURVE_AM3CRV = {
    "address": "0x445FE580eF8d70FF569aB36e80c647af338db351",
    "coins":   ["DAI", "USDCe", "USDT"],
}

_BALANCER_VAULT_ABI = [{
    "inputs": [{"name": "poolId", "type": "bytes32"}],
    "name": "getPoolTokens",
    "outputs": [
        {"name": "tokens", "type": "address[]"},
        {"name": "balances", "type": "uint256[]"},
        {"name": "lastChangeBlock", "type": "uint256"},
    ],
    "stateMutability": "view", "type": "function",
}]

_CURVE_3POOL_ABI = [
    {"inputs": [{"name": "i", "type": "uint256"}], "name": "balances",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "A",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "fee",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


# ---------------------------------------------------------------------------
# Curve StableSwap math (port of Vyper reference, generalised over n coins
# but used here only as a 2-coin pairwise view of an n-coin pool).
# ---------------------------------------------------------------------------

def _curve_get_D(balances: List[float], A: float) -> float:
    n = len(balances)
    S = sum(balances)
    if S == 0:
        return 0.0
    Ann = A * (n ** n)
    D = S
    for _ in range(255):
        D_P = D
        for x in balances:
            D_P = D_P * D / (x * n)
        D_prev = D
        D = (Ann * S + D_P * n) * D / ((Ann - 1) * D + (n + 1) * D_P)
        if abs(D - D_prev) <= 1e-9:
            break
    return D


def _curve_get_y(i: int, j: int, x_new: float,
                 balances: List[float], A: float, D: float) -> float:
    """Solve invariant for new balance of coin j given new balance of coin i."""
    n = len(balances)
    Ann = A * (n ** n)
    c = D
    S_ = 0.0
    for k in range(n):
        if k == j:
            continue
        _x = x_new if k == i else balances[k]
        S_ += _x
        c = c * D / (_x * n)
    c = c * D / (Ann * n)
    b = S_ + D / Ann
    y = D
    for _ in range(255):
        y_prev = y
        y = (y * y + c) / (2 * y + b - D)
        if abs(y - y_prev) <= 1e-9:
            break
    return y


def _curve_get_dy(i: int, j: int, dx: float,
                  balances: List[float], A: float, fee: float) -> float:
    """How much of coin j you receive for ``dx`` of coin i."""
    if dx <= 0:
        return 0.0
    D = _curve_get_D(balances, A)
    if D <= 0:
        return 0.0
    x_new = balances[i] + dx
    y_new = _curve_get_y(i, j, x_new, balances, A, D)
    dy = balances[j] - y_new
    return max(0.0, dy * (1.0 - fee))


# ---------------------------------------------------------------------------
# Balancer + Curve fetchers
# ---------------------------------------------------------------------------

def _addr_to_sym(addr: str) -> Optional[str]:
    """Reverse-lookup symbol for a token address."""
    al = addr.lower()
    for sym, (a, _d) in _TOKENS.items():
        if a.lower() == al:
            return sym
    return None


def _fetch_balancer_pool_pair(
    w3: Web3, pool_id: str, fee: float
) -> List["_PoolSnapshot"]:
    """Read a Balancer V2 50/50 pool's tokens + balances and return one
    ``_PoolSnapshot`` per registered token pair (always 1 for a 2-coin pool)."""
    try:
        vault = w3.eth.contract(
            address=Web3.to_checksum_address(_BALANCER_VAULT),
            abi=_BALANCER_VAULT_ABI,
        )
        tokens, balances, _ = vault.functions.getPoolTokens(pool_id).call()
        if len(tokens) != 2:
            return []  # only 50/50 weighted pools handled here
        syms = [_addr_to_sym(t) for t in tokens]
        if any(s is None for s in syms):
            return []
        decs = [_TOKENS[s][1] for s in syms]
        bals = [balances[i] / (10 ** decs[i]) for i in range(2)]
        # Canonical sort by address (matches our pair_key convention)
        if tokens[0].lower() > tokens[1].lower():
            syms = list(reversed(syms))
            bals = list(reversed(bals))
        if bals[0] <= 0 or bals[1] <= 0:
            return []
        # Pool address derives from poolId's leading 20 bytes
        pool_addr = "0x" + pool_id[2:42]
        return [_PoolSnapshot(
            pool_address=pool_addr,
            dex="balancer_w50",
            fee=fee,
            sym0=syms[0], sym1=syms[1],
            reserve0=bals[0], reserve1=bals[1],
            price=bals[1] / bals[0],
            kind="balancer_weighted",
            pool_id=pool_id,
        )]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Balancer pool fetch failed (%s): %s", pool_id, exc)
        return []


def _fetch_curve_3pool_views(
    w3: Web3, pool_addr: str, coin_syms: List[str]
) -> List["_PoolSnapshot"]:
    """Read am3CRV-style 3-coin pool and return one snapshot per coin pair."""
    try:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_addr), abi=_CURVE_3POOL_ABI,
        )
        decs = [_TOKENS[s][1] for s in coin_syms]
        raw_bals = [pool.functions.balances(i).call() for i in range(len(coin_syms))]
        balances = [raw_bals[i] / (10 ** decs[i]) for i in range(len(coin_syms))]
        amp = float(pool.functions.A().call())
        fee_raw = pool.functions.fee().call()
        fee = fee_raw / 1e10  # Curve fee is stored as 1e10-scaled
    except Exception as exc:  # noqa: BLE001
        logger.debug("Curve pool fetch failed (%s): %s", pool_addr, exc)
        return []

    out: List[_PoolSnapshot] = []
    n = len(coin_syms)
    for i, j in itertools.combinations(range(n), 2):
        si, sj = coin_syms[i], coin_syms[j]
        ai = _TOKENS[si][0].lower()
        aj = _TOKENS[sj][0].lower()
        # Canonical token0 = lower address (matches UniV3/V2 convention)
        if ai < aj:
            sym0, sym1, b0, b1, idx0, idx1 = si, sj, balances[i], balances[j], i, j
        else:
            sym0, sym1, b0, b1, idx0, idx1 = sj, si, balances[j], balances[i], j, i
        if b0 <= 0 or b1 <= 0:
            continue
        # Marginal spot price from a tiny probe swap â€” for StableSwap
        # the balance ratio is NOT the price; the invariant keeps the
        # swap rate near 1.0 even when balances are imbalanced.
        probe = max(0.001, min(b0, b1) * 1e-6)
        dy = _curve_get_dy(idx0, idx1, probe, balances, amp, fee)
        spot = (dy / probe) if probe > 0 and dy > 0 else 1.0
        out.append(_PoolSnapshot(
            pool_address=pool_addr,
            dex="curve_ss",
            fee=fee,
            sym0=sym0, sym1=sym1,
            reserve0=b0, reserve1=b1,
            price=spot,
            kind="curve_ss",
            amp=amp,
            coin0_index=idx0,
            coin1_index=idx1,
        ))
    return out

# UniV3 fee tiers to probe (in raw uint24 units: 100=0.01%, 500=0.05%, 3000=0.30%, 10000=1%)
_V3_FEE_TIERS = [100, 500, 3000, 10000]

# Null / zero address sentinel used by factory contracts
_NULL_ADDR = "0x" + "0" * 40

# Gas estimate for a 2-leg flash-loan arb on Polygon
_GAS_UNITS = 450_000

# ---------------------------------------------------------------------------
# Live scan: data structures
# ---------------------------------------------------------------------------

@dataclass
class _PoolSnapshot:
    """Price and liquidity snapshot for a single DEX pool."""
    pool_address: str
    dex: str              # e.g. "univ3_500", "qsv2", "balancer_w50", "curve_ss"
    fee: float            # as decimal, e.g. 0.003
    # token0/token1 symbols (sorted by address, matching factory ordering)
    sym0: str
    sym1: str
    # Decimal-normalised reserves (1 USDC = 1.0, 1 WETH = 1.0)
    reserve0: float
    reserve1: float
    # token1-per-token0 price (both in normalised units)
    price: float
    # Pool math kind: 'cpmm' (default) or 'curve_ss' (StableSwap 2-coin view).
    kind: str = "cpmm"
    # Curve amplification coefficient (ignored unless kind == 'curve_ss').
    amp: float = 0.0
    # Balancer vault poolId and Curve pool coin indexes for protocol-native quotes.
    pool_id: Optional[str] = None
    coin0_index: Optional[int] = None
    coin1_index: Optional[int] = None


@dataclass
class TokenPriceEvidence:
    """How a token USD price was derived for the current discovery cycle."""

    symbol: str
    price_usd: float
    source: str
    path: str
    hops: int
    edge_tvl_usd: float
    edge_bottleneck_usd: float


@dataclass
class PriceDiscoveryReport:
    """Per-scan price coverage and pool quarantine diagnostics."""

    generated_at: float
    discovered_tokens: List[str]
    priced_tokens: List[str]
    unpriced_tokens: List[str]
    prices_usd: Dict[str, float]
    evidence: Dict[str, Dict[str, Any]]
    quarantined_pools: List[Dict[str, Any]]
    quarantine_summary: Dict[str, int]


@dataclass
class OpportunityRecord:
    """Single cross-DEX opportunity observation."""
    scan_no: int
    timestamp: float
    pair: str
    buy_dex: str
    sell_dex: str
    buy_pool: str
    sell_pool: str
    buy_price_usdc: float
    sell_price_usdc: float
    spot_spread_bps: float
    executable_spread_bps: float
    raw_spread_bps: float
    flash_size_usd: float
    trade_size_usd: float
    gross_profit_usd: float
    slippage_cost_usd: float
    flash_fee_usd: float
    gas_cost_usd: float
    expected_net_edge: float   # USD token profit after route + flash fee; gas is owner-funded
    p_fill: float              # P(inclusion in next block) at optimal tip
    e_profit: float            # E[profit] = p_fill x owner net after gas/buffer (0 when edge <= 0)
    profitable: bool
    hop_count: int = 2         # Number of swap legs (2 = two-leg arb, 3 = triangular, …)
    route_tokens: str = ""
    route_pools: str = ""
    route_dexes: str = ""
    route_id: str = ""
    route_leg_amounts_in: str = ""
    route_leg_amounts_out: str = ""
    route_swap_0_to_1: str = ""
    route_pool_ids: str = ""
    route_curve_coin_indices: str = ""
    buy_leg1_price: float = 0.0
    sell_leg2_price: float = 0.0
    leg_price_executable_spread_abs: float = 0.0
    leg_price_executable_spread_bps: float = 0.0
    leg_price_invariant_status: str = "NOT_EVALUATED"
    leg_price_invariant_reason: str = "LEG_PRICE_INVARIANT_NOT_EVALUATED"
    leg_price_quote_block: int = 0
    weakest_pool_tvl_usd: float = 0.0
    route_tvl_cap_bps: float = 0.0
    route_size_cap_usd: float = 0.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _real_market_data_only() -> bool:
    return _env_bool("REAL_MARKET_DATA_ONLY", True)


def _assert_real_market_data_only_policy() -> None:
    if _real_market_data_only() and _env_bool("APEX_ALLOW_SYNTHETIC_TEST_DATA", False):
        raise RuntimeError(
            "REAL_MARKET_DATA_ONLY_VIOLATION: APEX_ALLOW_SYNTHETIC_TEST_DATA "
            "cannot be enabled for executable market discovery"
        )


def _assert_no_synthetic_route_records(records: List[OpportunityRecord]) -> None:
    if not _real_market_data_only():
        return
    for rec in records:
        route_text = "|".join(
            [
                str(rec.buy_pool),
                str(rec.sell_pool),
                str(rec.route_pools),
                str(rec.route_id),
            ]
        )
        if "0xSIM_" in route_text or "SIM_" in route_text:
            raise RuntimeError(
                f"REAL_MARKET_DATA_ONLY_VIOLATION: synthetic pool leaked into "
                f"route artifact {rec.route_id}"
            )


def _is_retryable_rpc_error(error: str) -> bool:
    text = str(error or "").lower()
    return any(
        marker in text
        for marker in (
            "429",
            "too many requests",
            "timeout",
            "connection",
            "temporarily unavailable",
            "rate limit",
            "service unavailable",
            "bad gateway",
            "gateway",
        )
    )


def _env_float_list(name: str, default: List[float]) -> List[float]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    values: List[float] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            values.append(float(part))
    return values or default


def _flash_size_candidates_usd(
    *,
    weaker_pool_tvl_usd: float,
    min_flash_loan_usd: float,
    max_flash_loan_usd: float,
    max_trade_size_usd: float,
    max_flash_tvl_fraction: float,
    scan_fractions: List[float],
) -> List[float]:
    """Build the executable flash-loan size from weakest-pool TVL.

    Production sizing is intentionally mechanical:
        flash_size_usd = min(
            min(pool TVLs) * max_flash_tvl_fraction,
            max_flash_loan_usd,
            max_trade_size_usd,
        )

    ``scan_fractions`` is accepted for backward API compatibility, but live
    sizing no longer searches a ladder here.
    """
    if weaker_pool_tvl_usd <= 0:
        return []
    size = min(
        weaker_pool_tvl_usd * max_flash_tvl_fraction,
        max_flash_loan_usd,
        max_trade_size_usd,
    )
    if size < min_flash_loan_usd:
        return []
    return [size]

# ---------------------------------------------------------------------------
# Live scan: on-chain helpers (synchronous, run in executor for async callers)
# ---------------------------------------------------------------------------

def _load_rpc_url() -> str:
    env_path = Path(__file__).parent / "apex_omega_core" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            pass
    urls = collect_rpc_urls(chain_id=137)
    return urls[0] if urls else os.getenv("POLYGON_RPC", "https://polygon-rpc.com/")


def _fetch_univ3_pool(
    w3: Web3, factory_addr: str, addr_a: str, addr_b: str, fee: int
) -> Optional[str]:
    """Return pool address or None if the pool doesn't exist."""
    try:
        factory = w3.eth.contract(
            address=Web3.to_checksum_address(factory_addr), abi=_UNIV3_FACTORY_ABI
        )
        pool = factory.functions.getPool(
            Web3.to_checksum_address(addr_a),
            Web3.to_checksum_address(addr_b),
            fee,
        ).call()
        return None if pool.lower() == _NULL_ADDR else pool
    except Exception as exc:
        logger.debug("UniV3 getPool failed (fee=%s): %s", fee, exc)
        return None


def _fetch_univ3_snapshot(
    w3: Web3,
    pool_addr: str,
    sym0: str,
    sym1: str,
    dec0: int,
    dec1: int,
    fee_raw: int,
) -> Optional[_PoolSnapshot]:
    """Fetch current price and virtual reserves from a UniV3 pool."""
    try:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_addr), abi=_UNIV3_POOL_ABI
        )
        slot0 = pool.functions.slot0().call()
        liquidity = pool.functions.liquidity().call()

        sqrt_price_x96 = slot0[0]
        if sqrt_price_x96 == 0 or liquidity == 0:
            return None

        # Virtual reserves at the active tick (constant-product approximation)
        sqrt_p = sqrt_price_x96 / (2 ** 96)
        vr0_raw = liquidity / sqrt_p          # token0 raw units
        vr1_raw = liquidity * sqrt_p          # token1 raw units

        reserve0 = vr0_raw / (10 ** dec0)
        reserve1 = vr1_raw / (10 ** dec1)

        # Decimal-adjusted price: token1_norm / token0_norm
        price_raw = sqrt_p ** 2
        price = price_raw * (10 ** dec0) / (10 ** dec1)

        dex_label = f"univ3_{fee_raw}"
        return _PoolSnapshot(
            pool_address=pool_addr,
            dex=dex_label,
            fee=fee_raw / 1_000_000,
            sym0=sym0,
            sym1=sym1,
            reserve0=reserve0,
            reserve1=reserve1,
            price=price,
        )
    except Exception as exc:
        logger.debug("UniV3 slot0 failed (%s): %s", pool_addr, exc)
        return None


def _fetch_qsv2_pair(
    w3: Web3, factory_addr: str, addr_a: str, addr_b: str
) -> Optional[str]:
    """Return pair address or None if the pair doesn't exist."""
    try:
        factory = w3.eth.contract(
            address=Web3.to_checksum_address(factory_addr), abi=_QSV2_FACTORY_ABI
        )
        pair = factory.functions.getPair(
            Web3.to_checksum_address(addr_a),
            Web3.to_checksum_address(addr_b),
        ).call()
        return None if pair.lower() == _NULL_ADDR else pair
    except Exception as exc:
        logger.debug("QSV2 getPair failed: %s", exc)
        return None


def _fetch_qsv2_snapshot(
    w3: Web3,
    pair_addr: str,
    sym0: str,
    sym1: str,
    dec0: int,
    dec1: int,
) -> Optional[_PoolSnapshot]:
    """Fetch reserves and price from a QuickSwap V2 (UniswapV2-style) pair."""
    try:
        pair = w3.eth.contract(
            address=Web3.to_checksum_address(pair_addr), abi=_QSV2_PAIR_ABI
        )
        reserves = pair.functions.getReserves().call()
        r0_raw, r1_raw = reserves[0], reserves[1]
        if r0_raw == 0 or r1_raw == 0:
            return None

        # Verify token ordering matches our expectation
        actual_t0 = pair.functions.token0().call().lower()
        expected_t0 = Web3.to_checksum_address(_TOKENS[sym0][0]).lower()
        if actual_t0 != expected_t0:
            # Token order is swapped â€“ flip reserves and symbols
            r0_raw, r1_raw = r1_raw, r0_raw
            sym0, sym1 = sym1, sym0
            dec0, dec1 = dec1, dec0

        reserve0 = r0_raw / (10 ** dec0)
        reserve1 = r1_raw / (10 ** dec1)
        price = reserve1 / reserve0

        return _PoolSnapshot(
            pool_address=pair_addr,
            dex="qsv2",
            fee=0.003,        # QuickSwap V2 fixed 0.3%
            sym0=sym0,
            sym1=sym1,
            reserve0=reserve0,
            reserve1=reserve1,
            price=price,
        )
    except Exception as exc:
        logger.debug("QSV2 getReserves failed (%s): %s", pair_addr, exc)
        return None


def _discover_pair(
    w3: Web3, sym_a: str, sym_b: str
) -> Tuple[str, List[_PoolSnapshot]]:
    """Discover all UniV3 + QSV2 pools for a single token pair.

    Pulled out as a standalone function so :func:`_discover_pools` can
    fan it out across a ThreadPoolExecutor.  Returns ``(pair_key, [])``
    if no pools were found so the caller can decide whether to keep it.
    """
    addr_a, dec_a = _TOKENS[sym_a]
    addr_b, dec_b = _TOKENS[sym_b]

    # Canonical token0/token1 ordering (lower address first)
    if addr_a.lower() < addr_b.lower():
        sym0, sym1, addr0, addr1, dec0, dec1 = sym_a, sym_b, addr_a, addr_b, dec_a, dec_b
    else:
        sym0, sym1, addr0, addr1, dec0, dec1 = sym_b, sym_a, addr_b, addr_a, dec_b, dec_a

    pair_key = f"{sym0}/{sym1}"
    pools: List[_PoolSnapshot] = []

    # UniV3 â€“ try all fee tiers
    for fee in _V3_FEE_TIERS:
        pool_addr = _fetch_univ3_pool(w3, _UNIV3_FACTORY, addr0, addr1, fee)
        if pool_addr:
            snap = _fetch_univ3_snapshot(w3, pool_addr, sym0, sym1, dec0, dec1, fee)
            if snap and snap.reserve0 > 0 and snap.reserve1 > 0:
                pools.append(snap)

    # QuickSwap V2
    qs_pair = _fetch_qsv2_pair(w3, _QSV2_FACTORY, addr0, addr1)
    if qs_pair:
        snap = _fetch_qsv2_snapshot(w3, qs_pair, sym0, sym1, dec0, dec1)
        if snap and snap.reserve0 > 0 and snap.reserve1 > 0:
            pools.append(snap)

    return pair_key, pools


def _discover_external_pools(w3: Web3) -> List["_PoolSnapshot"]:
    """Fetch Balancer V2 + Curve pools (registry-driven, not pair-by-pair).

    These DEXes don't expose a per-pair factory like UniV3/V2, so we
    enumerate known pool addresses once per scan and let the regular
    pair-bucketing in :func:`_discover_pools` slot each snapshot under
    its canonical ``"sym0/sym1"`` key.
    """
    out: List[_PoolSnapshot] = []
    for pool_id, fee in _BALANCER_W50_POOLS:
        out.extend(_fetch_balancer_pool_pair(w3, pool_id, fee))
    out.extend(_fetch_balancer_api_weighted_pairs())
    out.extend(_fetch_curve_3pool_views(
        w3, _CURVE_AM3CRV["address"], _CURVE_AM3CRV["coins"],
    ))
    deduped: Dict[Tuple[str, str, str], _PoolSnapshot] = {}
    for snap in out:
        deduped[(snap.pool_address.lower(), snap.sym0, snap.sym1)] = snap
    return list(deduped.values())


def _discover_pools(w3: Web3, max_workers: int = 12) -> Dict[str, List[_PoolSnapshot]]:
    """
    Query UniV3 and QuickSwap V2 for all configured token pairs *in
    parallel*.  web3.py is sync-IO-bound, so a ThreadPoolExecutor is
    sufficient to overlap RPC roundtrips without GIL contention.

    With 12 workers and ~13 pairs this drops a typical Polygon scan
    from ~54s sequential â†’ ~5-8s.
    """
    max_workers = max(1, int(_env_float("DISCOVERY_MAX_WORKERS", float(max_workers))))
    pair_timeout = max(1.0, _env_float("DISCOVERY_PAIR_TIMEOUT_SEC", 90.0))
    external_timeout = max(1.0, _env_float("DISCOVERY_EXTERNAL_TIMEOUT_SEC", 45.0))
    snapshots: Dict[str, List[_PoolSnapshot]] = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [pool.submit(_discover_pair, w3, a, b) for (a, b) in _PAIRS]
        ext_future = pool.submit(_discover_external_pools, w3)
        completed = 0
        try:
            iterator = as_completed(futures, timeout=pair_timeout)
            for fut in iterator:
                completed += 1
                try:
                    pair_key, pools = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("pair discovery failed: %s", exc)
                    continue
                if pools:
                    snapshots[pair_key] = pools
        except TimeoutError:
            pending = len(futures) - completed
            logger.warning(
                "Pair discovery timeout after %.1fs; using %d completed pairs, cancelling %d pending lookups.",
                pair_timeout,
                completed,
                pending,
            )
            for fut in futures:
                fut.cancel()
        # Merge Balancer + Curve snapshots into the same pair buckets
        try:
            external = ext_future.result(timeout=external_timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("external pool discovery skipped/failed after %.1fs: %s", external_timeout, exc)
            external = []
        for snap in external:
            key = f"{snap.sym0}/{snap.sym1}"
            snapshots.setdefault(key, []).append(snap)
        return snapshots
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


_STABLE_PRICE_ANCHORS: Dict[str, float] = {
    "USDC": 1.0,
    "USDCe": 1.0,
    "USDT": 1.0,
    "DAI": 1.0,
    "FRAX": 1.0,
    "MAI": 1.0,
    "TUSD": 1.0,
}

_PRICE_REPORT_PATH = Path("runtime") / "price_discovery_report.json"
_LIVE_QUOTE_DIAGNOSTICS_PATH = Path("runtime") / "live_quote_near_misses.json"


def _json_safe(value: Any) -> Any:
    """Convert runtime artifacts to strict JSON-safe values.

    Python's json module will otherwise serialize non-finite floats as
    Infinity/NaN, which browser JSON parsers reject. The scanner can still use
    infinities internally for graph ranking, but persisted artifacts must be
    standards-compliant.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _external_price_request(url: str, *, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    req_headers = {
        "Accept": "application/json",
        "User-Agent": "Apex-Omega-v6/1.0 PriceDiscovery",
    }
    if headers:
        req_headers.update({k: v for k, v in headers.items() if v})
    try:
        with urlopen(Request(url, headers=req_headers), timeout=float(os.getenv("PRICE_API_TIMEOUT_SEC", "8"))) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("External price request failed (%s): %s", url.split("?")[0], exc)
        return None


def _fetch_coingecko_token_prices(discovered_tokens: List[str]) -> Dict[str, float]:
    if not _env_bool("COINGECKO_PRICE_ENABLED", True):
        return {}
    symbols = [
        sym
        for sym in discovered_tokens
        if sym in _TOKENS and sym not in _STABLE_PRICE_ANCHORS
    ]
    if not symbols:
        return {}
    base_api = os.getenv("COINGECKO_API", "https://api.coingecko.com/api/v3").rstrip("/")
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    out: Dict[str, float] = {}
    chunk_size = max(1, min(50, int(float(os.getenv("COINGECKO_PRICE_CHUNK_SIZE", "40")))))
    for idx in range(0, len(symbols), chunk_size):
        chunk = symbols[idx:idx + chunk_size]
        addr_to_sym = {_TOKENS[sym][0].lower(): sym for sym in chunk}
        params = urlencode({
            "contract_addresses": ",".join(addr_to_sym),
            "vs_currencies": "usd",
        })
        data = _external_price_request(f"{base_api}/simple/token_price/polygon-pos?{params}", headers=headers)
        if not isinstance(data, dict):
            continue
        for addr, payload in data.items():
            sym = addr_to_sym.get(str(addr).lower())
            if not sym or not isinstance(payload, dict):
                continue
            try:
                price = float(payload.get("usd") or 0.0)
            except Exception:
                continue
            if price > 0.0 and math.isfinite(price):
                out[sym] = price
    return out


def _fetch_moralis_token_prices(symbols: List[str]) -> Dict[str, float]:
    api_key = os.getenv("MORALIS_API_KEY", "").strip()
    if not api_key or not _env_bool("MORALIS_PRICE_ENABLED", True):
        return {}
    base_api = os.getenv("MORALIS_API", "https://deep-index.moralis.io/api/v2.2").rstrip("/")
    max_tokens = max(0, min(25, int(float(os.getenv("MORALIS_PRICE_MAX_TOKENS", "12")))))
    out: Dict[str, float] = {}
    for sym in symbols[:max_tokens]:
        if sym not in _TOKENS or sym in _STABLE_PRICE_ANCHORS:
            continue
        addr = _TOKENS[sym][0]
        params = urlencode({"chain": "polygon"})
        data = _external_price_request(
            f"{base_api}/erc20/{addr}/price?{params}",
            headers={"X-API-Key": api_key},
        )
        if not isinstance(data, dict):
            continue
        try:
            price = float(data.get("usdPrice") or data.get("usd_price") or 0.0)
        except Exception:
            continue
        if price > 0.0 and math.isfinite(price):
            out[sym] = price
    return out


def _fetch_external_token_prices(discovered_tokens: List[str]) -> Dict[str, Tuple[float, str]]:
    if not _env_bool("PRICE_EXTERNAL_ENABLED", True):
        return {}
    prices: Dict[str, Tuple[float, str]] = {}
    for sym, price in _fetch_coingecko_token_prices(discovered_tokens).items():
        prices[sym] = (price, "coingecko_token_price")
    missing = [sym for sym in discovered_tokens if sym not in prices and sym not in _STABLE_PRICE_ANCHORS]
    for sym, price in _fetch_moralis_token_prices(missing).items():
        prices.setdefault(sym, (price, "moralis_token_price"))
    return prices


def _snapshot_tvl_usd(snap: "_PoolSnapshot", token_prices: Dict[str, float]) -> float:
    return (
        max(0.0, float(snap.reserve0)) * max(0.0, float(token_prices.get(snap.sym0, 0.0)))
        + max(0.0, float(snap.reserve1)) * max(0.0, float(token_prices.get(snap.sym1, 0.0)))
    )


def _derive_token_prices_with_report(
    pool_map: Dict[str, List["_PoolSnapshot"]]
) -> Tuple[Dict[str, float], PriceDiscoveryReport]:
    """Derive live USD token prices through the discovered pool graph.

    The scan is fail-closed for execution: no hardcoded token prices are used.
    Stablecoins seed the graph at $1.00; all other prices must be derived from
    real pools discovered in this scan.
    """
    discovered_tokens = sorted(
        {
            sym
            for pools in pool_map.values()
            for snap in pools
            for sym in (snap.sym0, snap.sym1)
        }
    )
    prices: Dict[str, float] = {}
    evidence: Dict[str, TokenPriceEvidence] = {}
    frontier: List[Tuple[float, str]] = []

    for sym, price in _STABLE_PRICE_ANCHORS.items():
        if sym in discovered_tokens or sym in _TOKENS:
            prices[sym] = price
            evidence[sym] = TokenPriceEvidence(
                symbol=sym,
                price_usd=price,
                source="stable_anchor",
                path=sym,
                hops=0,
                edge_tvl_usd=float("inf"),
                edge_bottleneck_usd=float("inf"),
            )
            frontier.append((float("inf"), sym))

    # Edges are price propagation multipliers: price[to] = price[from] * multiplier.
    # Each edge carries reserves in the traversal direction so evidence can be
    # scored by the weaker USD side of the pool, not misleading raw token units.
    graph: Dict[str, List[Tuple[str, float, float, float, str]]] = {}
    for pools in pool_map.values():
        for snap in pools:
            if snap.price <= 0 or not math.isfinite(snap.price):
                continue
            if snap.reserve0 <= 0 or snap.reserve1 <= 0:
                continue
            graph.setdefault(snap.sym0, []).append(
                (snap.sym1, 1.0 / float(snap.price), float(snap.reserve0), float(snap.reserve1), snap.pool_address)
            )
            graph.setdefault(snap.sym1, []).append(
                (snap.sym0, float(snap.price), float(snap.reserve1), float(snap.reserve0), snap.pool_address)
            )

    min_edge_bottleneck_usd = _env_float("PRICE_EDGE_MIN_BOTTLENECK_USD", 100.0)
    max_price_hops = int(_env_float("PRICE_GRAPH_MAX_HOPS", 4.0))
    best_score: Dict[str, float] = {sym: float("inf") for sym in prices}
    frontier.sort(reverse=True)

    while frontier:
        score, current = frontier.pop(0)
        if score < best_score.get(current, 0.0):
            continue
        current_price = prices[current]
        current_evidence = evidence[current]
        if current_evidence.hops >= max_price_hops:
            continue
        for nxt, multiplier, current_reserve, next_reserve, pool_address in graph.get(current, []):
            if nxt in _STABLE_PRICE_ANCHORS:
                continue
            derived = current_price * multiplier
            if derived <= 0 or not math.isfinite(derived):
                continue
            current_side_usd = current_reserve * current_price
            next_side_usd = next_reserve * derived
            edge_tvl_usd = current_side_usd + next_side_usd
            edge_bottleneck_usd = min(current_side_usd, next_side_usd)
            candidate_score = min(score, edge_bottleneck_usd)
            if edge_bottleneck_usd < min_edge_bottleneck_usd:
                continue
            if candidate_score <= best_score.get(nxt, 0.0):
                continue
            prices[nxt] = derived
            best_score[nxt] = candidate_score
            evidence[nxt] = TokenPriceEvidence(
                symbol=nxt,
                price_usd=derived,
                source=f"pool:{pool_address}",
                path=f"{current_evidence.path}->{nxt}",
                hops=current_evidence.hops + 1,
                edge_tvl_usd=edge_tvl_usd,
                edge_bottleneck_usd=edge_bottleneck_usd,
            )
            frontier.append((candidate_score, nxt))
        frontier.sort(reverse=True)

    external_prices = _fetch_external_token_prices(discovered_tokens)
    for sym, (price, source) in external_prices.items():
        if sym not in discovered_tokens or sym in prices:
            continue
        prices[sym] = price
        evidence[sym] = TokenPriceEvidence(
            symbol=sym,
            price_usd=price,
            source=source,
            path=sym,
            hops=0,
            edge_tvl_usd=float("inf"),
            edge_bottleneck_usd=float("inf"),
        )

    priced_tokens = sorted(sym for sym in discovered_tokens if sym in prices)
    unpriced_tokens = sorted(sym for sym in discovered_tokens if sym not in prices)
    report = PriceDiscoveryReport(
        generated_at=time.time(),
        discovered_tokens=discovered_tokens,
        priced_tokens=priced_tokens,
        unpriced_tokens=unpriced_tokens,
        prices_usd={sym: round(float(prices[sym]), 12) for sym in sorted(prices) if sym in discovered_tokens},
        evidence={sym: asdict(evidence[sym]) for sym in sorted(evidence) if sym in discovered_tokens},
        quarantined_pools=[],
        quarantine_summary={},
    )
    return prices, report


def _filter_pool_universe_with_report(
    pool_map: Dict[str, List["_PoolSnapshot"]],
    token_prices: Dict[str, float],
    min_tvl_usd: float = 0.0,
    max_price_dev: float = 0.05,
    price_report: Optional[PriceDiscoveryReport] = None,
    require_multiple_venues: bool = True,
) -> Tuple[Dict[str, List["_PoolSnapshot"]], PriceDiscoveryReport]:
    """Drop unusable pools with explicit diagnostics instead of silent loss."""
    _ = max_price_dev
    report = price_report or PriceDiscoveryReport(
        generated_at=time.time(),
        discovered_tokens=sorted(
            {
                sym
                for pools in pool_map.values()
                for snap in pools
                for sym in (snap.sym0, snap.sym1)
            }
        ),
        priced_tokens=sorted(token_prices),
        unpriced_tokens=[],
        prices_usd={sym: round(float(price), 12) for sym, price in sorted(token_prices.items())},
        evidence={},
        quarantined_pools=[],
        quarantine_summary={},
    )
    cleaned: Dict[str, List["_PoolSnapshot"]] = {}
    summary: Dict[str, int] = {}

    def quarantine(reason: str, pair_key: str, snap: Optional["_PoolSnapshot"] = None, **extra: Any) -> None:
        summary[reason] = summary.get(reason, 0) + 1
        payload: Dict[str, Any] = {"reason": reason, "pair": pair_key, **extra}
        if snap is not None:
            payload.update(
                {
                    "pool": snap.pool_address,
                    "dex": snap.dex,
                    "token0": snap.sym0,
                    "token1": snap.sym1,
                    "reserve0": snap.reserve0,
                    "reserve1": snap.reserve1,
                    "price": snap.price,
                }
            )
        report.quarantined_pools.append(payload)

    for pair_key, pools in pool_map.items():
        if require_multiple_venues and len(pools) < 2:
            quarantine("insufficient_discovered_venues", pair_key, venue_count=len(pools))
            continue

        liquid: List["_PoolSnapshot"] = []
        for snap in pools:
            missing = [
                sym
                for sym in (snap.sym0, snap.sym1)
                if token_prices.get(sym, 0.0) <= 0 or not math.isfinite(token_prices.get(sym, 0.0))
            ]
            if missing:
                quarantine("missing_token_price", pair_key, snap, missing_tokens=missing)
                continue
            tvl_usd = _snapshot_tvl_usd(snap, token_prices)
            if min_tvl_usd > 0.0 and tvl_usd < min_tvl_usd:
                quarantine("below_min_tvl", pair_key, snap, tvl_usd=round(tvl_usd, 6), min_tvl_usd=min_tvl_usd)
                continue
            liquid.append(snap)
        if require_multiple_venues and len(liquid) < 2:
            quarantine("insufficient_usable_venues", pair_key, usable_venue_count=len(liquid))
            continue
        if not require_multiple_venues and not liquid:
            quarantine("no_liquid_graph_edge", pair_key, usable_venue_count=0)
            continue
        cleaned[pair_key] = liquid

    report.quarantine_summary = dict(sorted(summary.items()))
    return cleaned, report


def _write_price_discovery_report(report: PriceDiscoveryReport, output_path: Optional[Path] = None) -> None:
    path = output_path or _PRICE_REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_safe(asdict(report))
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _write_live_quote_diagnostics(payload: Dict[str, Any]) -> None:
    try:
        _LIVE_QUOTE_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LIVE_QUOTE_DIAGNOSTICS_PATH.write_text(
            json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("live quote diagnostics write failed: %s", exc)


def _filter_pool_universe(
    pool_map: Dict[str, List["_PoolSnapshot"]],
    token_prices: Dict[str, float],
    min_tvl_usd: float = 0.0,
    max_price_dev: float = 0.05,
) -> Dict[str, List["_PoolSnapshot"]]:
    """Drop stale / mis-priced pools before scoring.

    Filter:
      **Liquidity gate** - drop pools below ``min_tvl_usd`` using current
      reserve valuation from the discovered token price map.

    ``max_price_dev`` is retained for API compatibility only. Discovery no
    longer rejects pools solely because their price differs from a median
    anchor; execution eligibility is decided by liquidity, route math,
    repayment, gas, and owner-net profit.
    """
    cleaned, _ = _filter_pool_universe_with_report(
        pool_map,
        token_prices,
        min_tvl_usd=min_tvl_usd,
        max_price_dev=max_price_dev,
    )
    return cleaned


def _derive_token_prices_usd(
    pool_map: Dict[str, List[_PoolSnapshot]]
) -> Dict[str, float]:
    """
    Estimate USD prices for each token.
    Stablecoins are pegged at $1.00. Other tokens are derived from the
    discovered pool graph. No hardcoded token fallbacks are used in live scan.
    """
    prices, _ = _derive_token_prices_with_report(pool_map)
    return prices


def _dex_type_for_slippage(dex_name: str) -> str:
    """Map a pool's dex field to a DEX type understood by calculate_deterministic_slippage_bps.

    Returns ``"v3"`` for any concentrated-liquidity Uniswap/QuickSwap V3 variant,
    ``"aerodrome"`` for Aerodrome / Solidly vAMM pools, and ``"v2"`` for all other
    constant-product pools (default).
    """
    name = dex_name.lower().replace("-", "_").replace(" ", "_")
    if "v3" in name or "univ3" in name or "quickswap_v3" in name or "algebra" in name:
        return "v3"
    if "aerodrome" in name or "solidly" in name or "velodrome" in name:
        return "aerodrome"
    return "v2"



def _pool_token1_price_usd(pool: "_PoolSnapshot", token0_usd: float) -> float:
    """Return USD per token1 for a pool whose snapshot price is token1 per token0.

    This is the pool-normalization boundary. The canonical spread block
    consumes only USD-per-token prices and does not contain pool-ratio logic.
    """
    if pool.price <= 0.0 or token0_usd <= 0.0:
        return 0.0
    return token0_usd / pool.price

def _compute_opportunity(
    scan_no: int,
    pair_key: str,
    buy: _PoolSnapshot,
    sell: _PoolSnapshot,
    token_prices: Dict[str, float],
    sentinel: SlippageSentinel,
    tip_optimizer: TipOptimizer,
    trade_size_usd: float,
    min_spread_bps: float = 0.0,
    min_net_profit_usd: float = 2.0,
    flash_loan_fee_rate: float = 0.0009,
    min_flash_loan_usd: float = 50.0,
    max_flash_loan_usd: float = 1_000_000.0,
    max_flash_tvl_fraction: float = 0.15,
    flash_size_scan_fractions: Optional[List[float]] = None,
) -> Optional[OpportunityRecord]:
    """
    Compute expected_net_edge, p_fill, and E[profit] for a single
    cross-DEX price discrepancy.  Returns None when spread is below the
    minimum threshold or reserves are too thin to simulate.
    """
    # SAFETY: this scorer assumes constant-product (CPMM) math.  Curve
    # StableSwap pools have ``kind == 'curve_ss'`` and are scored by
    # the triangular cycle search via :func:`_pool_swap_out`, which
    # dispatches correctly.  Mixing kinds here produced fake 8%+ spreads
    # because Curve's imbalanced reserves are NOT a price gap.
    if buy.kind != "cpmm" or sell.kind != "cpmm":
        return None
    # Uniswap V3/Algebra balances are not CPMM reserves.  They are total token
    # balances across concentrated ticks; profitable execution depends on the
    # active tick liquidity along the exact path.  Treating these balances as
    # x*y=k created false LINK/UNI candidates that failed the live quoter by
    # orders of magnitude.  V3 routes remain executable only after live quoter
    # validation in the payload builder; this dry two-pool scorer is V2-style
    # CPMM only.
    if _dex_type_for_slippage(buy.dex) == "v3" or _dex_type_for_slippage(sell.dex) == "v3":
        return None

    sym0, sym1 = pair_key.split("/")
    price0 = token_prices.get(sym0, 1.0)
    price1 = token_prices.get(sym1, 1.0)

    # =============================================================================
    # CANONICAL RAW SPREAD â€” USD PER TOKENA ONLY
    # =============================================================================
    # Required units:
    #   P_buy_usd  = lowest executable ask for TokenA, in USD / TokenA
    #   P_sell_usd = highest executable bid for TokenA, in USD / TokenA
    #
    # Raw discovery:
    #   delta_p_raw_usd = P_sell_usd - P_buy_usd
    #   raw_spread_bps  = (delta_p_raw_usd / P_buy_usd) * 10_000
    #   raw_profit_usd  = L_usd * (delta_p_raw_usd / P_buy_usd)
    #
    # Pool-specific reserve ratios are normalized before this block.
    # This block only subtracts USD-per-token prices.
    # =============================================================================
    p_buy_usd = _pool_token1_price_usd(buy, price0)
    p_sell_usd = _pool_token1_price_usd(sell, price0)

    if p_buy_usd <= 0.0 or p_sell_usd <= 0.0:
        return None

    delta_p_raw_usd = p_sell_usd - p_buy_usd
    spot_spread_bps = (delta_p_raw_usd / p_buy_usd) * 10_000.0

    if spot_spread_bps < min_spread_bps:
        return None

    raw_profit_usd = trade_size_usd * (delta_p_raw_usd / p_buy_usd)

    # raw_spread_bps starts as raw discovery; upgraded to executable below.
    raw_spread_bps = spot_spread_bps

    # ------------------------------------------------------------------
    # Flash-loan sizing: exactly 15% of the weakest pool TVL by default.
    # This keeps the execution size mechanically tied to available depth.
    # ------------------------------------------------------------------
    buy_tvl_usd = buy.reserve0 * price0 + buy.reserve1 * price1
    sell_tvl_usd = sell.reserve0 * price0 + sell.reserve1 * price1
    size_candidates_usd = _flash_size_candidates_usd(
        weaker_pool_tvl_usd=min(buy_tvl_usd, sell_tvl_usd),
        min_flash_loan_usd=min_flash_loan_usd,
        max_flash_loan_usd=max_flash_loan_usd,
        max_trade_size_usd=trade_size_usd,
        max_flash_tvl_fraction=max_flash_tvl_fraction,
        scan_fractions=flash_size_scan_fractions or [0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20],
    )
    if not size_candidates_usd:
        return None

    best_amount_in = 0.0
    best_size_usd = 0.0
    best_expected_net = -math.inf
    owner_profit_buffer_usd = max(
        0.0,
        _env_float("OWNER_PROFIT_BUFFER_USD", _env_float("MEV_BUFFER_USD", 0.0)),
    )
    best_ranking_edge = -math.inf
    for candidate_size_usd in size_candidates_usd:
        candidate_amount_in = candidate_size_usd / price0
        if candidate_amount_in <= 0.0:
            continue
        candidate_b_out = sentinel.amm_swap(candidate_amount_in, buy.reserve0, buy.reserve1, buy.fee)
        if candidate_b_out <= 0.0:
            continue
        candidate_a_out = sentinel.amm_swap(candidate_b_out, sell.reserve1, sell.reserve0, sell.fee)
        candidate_size_actual_usd = candidate_amount_in * price0
        candidate_gross = candidate_a_out * price0 - candidate_size_actual_usd
        candidate_flash_fee = candidate_size_actual_usd * flash_loan_fee_rate
        candidate_token_net = candidate_gross - candidate_flash_fee
        candidate_eip1559 = tip_optimizer.build_eip1559_params(max(candidate_token_net, 0.01))
        candidate_ranking_edge = (
            candidate_token_net
            - candidate_eip1559["gas_cost_usd"]
            - owner_profit_buffer_usd
        )
        if candidate_ranking_edge > best_ranking_edge:
            best_amount_in = candidate_amount_in
            best_size_usd = candidate_size_actual_usd
            best_expected_net = candidate_token_net
            best_ranking_edge = candidate_ranking_edge
    if best_size_usd < min_flash_loan_usd or best_ranking_edge < min_net_profit_usd:
        return None
    amount_in = best_amount_in
    actual_trade_size_usd = best_size_usd

    # ------------------------------------------------------------------
    # Cycle-best executable prices at the selected flash-loan size.
    #
    # These are the real AMM-output prices with fee and price-impact
    # already baked in â€” not the spot reserve ratios used above.
    # Wiring them into every route leg ensures that C1, C2, the pipeline
    # audit, and the dashboard all read the same cycle-lowest buy price
    # and cycle-highest sell price without needing to re-simulate.
    #
    #   best_buy_price_exec  = token0 paid  per token1 received  (lower  = better buy)
    #   best_sell_price_exec = token0 received per token1 sold   (higher = better sell)
    #
    # Profit formula (per unit of token1):
    #   profit_per_t1 = best_sell_price_exec âˆ’ best_buy_price_exec âˆ’ tx_costs
    # ------------------------------------------------------------------
    b_out_1_est = sentinel.amm_swap(amount_in, buy.reserve0, buy.reserve1, buy.fee)
    if b_out_1_est <= 0.0:
        return None
    best_buy_price_exec = amount_in / b_out_1_est            # token0 per token1 (lower = better buy)
    a_out_2_est = sentinel.amm_swap(b_out_1_est, sell.reserve1, sell.reserve0, sell.fee)
    best_sell_price_exec = a_out_2_est / b_out_1_est         # token0 per token1 (higher = better sell)

    # Upgrade raw_spread_bps from spot-based to executable-based.
    # The executable spread is strictly more conservative (smaller) because
    # it already embeds DEX fees and price impact on both legs.  Using it
    # for the slippage gate and the OpportunityRecord gives a more accurate
    # picture of the true edge that will be captured on execution.
    if best_buy_price_exec > 0.0:
        raw_spread_bps = (
            (best_sell_price_exec - best_buy_price_exec) / best_buy_price_exec * 10_000.0
        )

    # Skip pools whose active depth is clearly insufficient
    if buy.reserve0 < amount_in * 0.01 or sell.reserve1 < (amount_in * buy.price) * 0.01:
        return None

    # ------------------------------------------------------------------
    # Deterministic slippage pre-check (CPMM average-execution impact).
    # Compute worst-case leg slippage using constant-product math before
    # running the full route simulation.  This eliminates routes where
    # the pool is too shallow to absorb the trade â€” without relying on
    # heuristics or hard clamps.  buy_tvl_usd = reserve0 * 2 * price0
    # (balanced 50/50 pool assumption); sell_tvl_usd is analogous.
    # ------------------------------------------------------------------
    buy_tvl_usd = buy.reserve0 * 2.0 * price0
    sell_tvl_usd = sell.reserve1 * 2.0 * price1

    # Map DEX identifier to geometry category
    def _dex_cat(dex_id: str) -> str:
        dl = dex_id.lower()
        if "v3" in dl or "univ3" in dl:
            return "v3"
        if "aerodrome" in dl or "velodrome" in dl:
            return "aerodrome"
        return "v2"

    buy_slip_bps = calculate_deterministic_slippage_bps(
        trade_size=actual_trade_size_usd,
        pool_tvl=buy_tvl_usd,
        dex=_dex_cat(buy.dex),
        fee_bps=buy.fee * 10_000.0,
    )
    sell_slip_bps = calculate_deterministic_slippage_bps(
        trade_size=actual_trade_size_usd,
        pool_tvl=sell_tvl_usd,
        dex=_dex_cat(sell.dex),
        fee_bps=sell.fee * 10_000.0,
    )
    # Gate: combined slippage must not exceed the raw spread.  If it
    # does, the trade is underwater before gas and flash-loan fees.
    combined_slip_bps = buy_slip_bps + sell_slip_bps
    if combined_slip_bps >= raw_spread_bps:
        return None

    # 2-leg route: token0 â†’ token1 on buy pool, then token1 â†’ token0 on sell pool.
    # Each leg carries the cycle-best executable prices so every downstream
    # consumer (C1, C2, pipeline audit, dashboard) can read the lowest buy
    # and highest sell price for this cycle directly from the artifact.
    route = [
        {
            "venue": buy.dex,
            "pair": f"{sym0} â†’ {sym1}",
            "reserve_in": buy.reserve0,
            "reserve_out": buy.reserve1,
            "fee": buy.fee,
            "price_in_usd": price0,
            "price_out_usd": price1,
            # Cycle-lowest buy price: token0 paid per token1 received (fee + impact baked in)
            "best_buy_price_exec": best_buy_price_exec,
        },
        {
            "venue": sell.dex,
            "pair": f"{sym1} â†’ {sym0}",
            "reserve_in": sell.reserve1,
            "reserve_out": sell.reserve0,
            "fee": sell.fee,
            "price_in_usd": price1,
            "price_out_usd": price0,
            # Cycle-highest sell price: token0 received per token1 sold (fee + impact baked in)
            "best_sell_price_exec": best_sell_price_exec,
        },
    ]

    final_out, slippage_legs = sentinel.simulate_route(amount_in, route)

    initial_usd = actual_trade_size_usd
    final_usd = final_out * price0

    gross_profit = final_usd - initial_usd
    total_slippage = sum(
        float(leg.get("usd_in", 0)) - float(leg.get("usd_out", 0))
        for leg in slippage_legs
    )
    slippage_cost = max(0.0, total_slippage)

    # Flash-loan fee on the principal actually borrowed.  Provider is
    # configurable: Balancer = 0 bps, Aave V3 = 9 bps, etc.
    flash_fee = actual_trade_size_usd * flash_loan_fee_rate
    adjusted_gross = gross_profit - flash_fee

    # Gas cost and P(fill) at the optimal EIP-1559 tip
    eip1559 = tip_optimizer.build_eip1559_params(max(adjusted_gross, 0.01))
    gas_cost = eip1559["gas_cost_usd"]
    p_fill = eip1559["p_fill"]

    expected_net_edge = adjusted_gross
    ranking_edge = expected_net_edge - gas_cost - owner_profit_buffer_usd
    e_profit = ranking_edge * p_fill if ranking_edge > 0 else 0.0

    # Contract profit is still tracked separately as expected_net_edge because
    # gas is owner-paid. C1 eligibility requires owner net after gas/buffer.
    if ranking_edge < min_net_profit_usd:
        return None

    leg_price = _leg_price_invariant(
        leg_amounts_in=[amount_in, b_out_1_est],
        leg_amounts_out=[b_out_1_est, final_out],
    )
    if _leg_price_has_structural_failure(leg_price):
        return None

    return OpportunityRecord(
        scan_no=scan_no,
        timestamp=time.time(),
        pair=pair_key,
        buy_dex=buy.dex,
        sell_dex=sell.dex,
        buy_pool=buy.pool_address,
        sell_pool=sell.pool_address,
        buy_price_usdc=round(best_buy_price_exec * price0, 8),
        sell_price_usdc=round(best_sell_price_exec * price0, 8),
        spot_spread_bps=round(spot_spread_bps, 4),
        executable_spread_bps=round(raw_spread_bps, 4),
        raw_spread_bps=round(raw_spread_bps, 4),
        flash_size_usd=round(actual_trade_size_usd, 2),
        trade_size_usd=round(actual_trade_size_usd, 2),
        gross_profit_usd=round(gross_profit, 4),
        slippage_cost_usd=round(slippage_cost, 4),
        flash_fee_usd=round(flash_fee, 4),
        gas_cost_usd=round(gas_cost, 4),
        expected_net_edge=round(expected_net_edge, 4),
        p_fill=round(p_fill, 4),
        e_profit=round(e_profit, 4),
        profitable=(ranking_edge >= min_net_profit_usd),
        route_tokens=f"{buy.sym0}->{buy.sym1}->{sell.sym0}",
        route_pools=f"{buy.pool_address}->{sell.pool_address}",
        route_dexes=f"{buy.dex}->{sell.dex}",
        route_leg_amounts_in=json.dumps([amount_in, b_out_1_est]),
        route_leg_amounts_out=json.dumps([b_out_1_est, final_out]),
        route_swap_0_to_1=json.dumps([True, False]),
        route_pool_ids=json.dumps([None, None]),
        route_curve_coin_indices=json.dumps([None, None]),
        **leg_price,
    )


# ---------------------------------------------------------------------------
# Offline simulation helpers — TESTING ONLY, NOT used in the live scan path
#
# _SIM_TEMPLATES and _simulate_pools exist for offline unit tests and
# deterministic benchmarks.  The live scan path (run_live_opportunity_scan)
# uses _discover_pools() to fetch real on-chain data; these templates are
# never referenced in that path. They are also hard-gated by env so accidental
# runtime use fails closed.
# ---------------------------------------------------------------------------

# Pool templates: (pair_key, dex_a, fee_a, dex_b, fee_b,
#                  tvl_usd_a, tvl_usd_b, base_price,
#                  spread_bps_mean, spread_bps_std)
# Derived from historical Polygon DEX liquidity and spread data.
_SIM_TEMPLATES = [
    # Stablecoin pairs â€” very tight spreads, high TVL
    # base_price = AMM ratio: token1_normalised / token0_normalised
    #            = price_token0_usd / price_token1_usd
    ("USDC/USDT", "univ3_100",  0.0001, "qsv2",        0.003,  8_000_000,  3_000_000, 1.0,       1.0,  0.5),
    ("USDC/USDT", "univ3_500",  0.0005, "univ3_100",   0.0001, 4_000_000,  8_000_000, 1.0,       0.6,  0.3),
    ("USDC/DAI",  "univ3_100",  0.0001, "qsv2",        0.003,  5_000_000,  1_200_000, 1.0,       1.2,  0.6),
    ("USDT/DAI",  "univ3_100",  0.0001, "univ3_500",   0.0005, 2_000_000,  1_500_000, 1.0,       0.8,  0.4),
    # MATIC/stable pairs â€” moderate spread, medium TVL
    # WMATIC($0.40)/USDC($1.00): ratio = 0.40/1.0 = 0.40 USDC per WMATIC
    ("WMATIC/USDC", "univ3_500",  0.0005, "qsv2",        0.003,  6_000_000,  4_000_000, 0.40,      8.0,  4.0),
    ("WMATIC/USDC", "univ3_3000", 0.003,  "univ3_500",   0.0005, 1_500_000,  6_000_000, 0.40,     12.0,  5.0),
    ("WMATIC/USDT", "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  2_000_000, 0.40,      9.0,  4.5),
    ("WMATIC/DAI",  "univ3_500",  0.0005, "qsv2",        0.003,  1_500_000,  800_000,   0.40,     11.0,  5.0),
    # ETH/stable pairs â€” moderate spread, high TVL
    # USDC($1)/WETH($2500): ratio = 1.0/2500 = 0.0004 WETH per USDC
    ("USDC/WETH",   "univ3_500",  0.0005, "qsv2",        0.003,  9_000_000,  5_000_000, 4.0e-4,    6.0,  3.5),
    ("USDC/WETH",   "univ3_3000", 0.003,  "univ3_500",   0.0005, 2_500_000,  9_000_000, 4.0e-4,   14.0,  6.0),
    ("USDT/WETH",   "univ3_500",  0.0005, "qsv2",        0.003,  4_000_000,  3_000_000, 4.0e-4,    7.0,  3.5),
    ("DAI/WETH",    "univ3_500",  0.0005, "qsv2",        0.003,  2_000_000,  1_500_000, 4.0e-4,    8.5,  4.0),
    # WMATIC($0.40)/WETH($2500): ratio = 0.40/2500 = 1.6e-4 WETH per WMATIC
    ("WMATIC/WETH", "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  2_000_000, 1.6e-4,   10.0,  5.0),
    # BTC pairs â€” wider spreads due to lower liquidity
    # USDC($1)/WBTC($65000): ratio = 1/65000 â‰ˆ 1.538e-5 WBTC per USDC
    ("USDC/WBTC",   "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  1_200_000, 1.538e-5, 15.0,  8.0),
    ("USDC/WBTC",   "univ3_3000", 0.003,  "univ3_500",   0.0005, 800_000,    3_000_000, 1.538e-5, 22.0,  9.0),
    # WETH($2500)/WBTC($65000): ratio = 2500/65000 â‰ˆ 0.0385 WBTC per WETH
    ("WETH/WBTC",   "univ3_500",  0.0005, "qsv2",        0.003,  2_000_000,  900_000,   0.0385,   18.0,  8.0),
    # DeFi tokens â€” widest spreads, thinner liquidity
    # USDC($1)/LINK($12): ratio = 1/12 â‰ˆ 0.0833 LINK per USDC
    ("USDC/LINK",   "univ3_3000", 0.003,  "qsv2",        0.003,  1_000_000,  600_000,   0.0833,   25.0, 12.0),
    # WMATIC($0.40)/LINK($12): ratio = 0.40/12 â‰ˆ 0.0333 LINK per WMATIC
    ("WMATIC/LINK", "univ3_3000", 0.003,  "qsv2",        0.003,  500_000,    400_000,   0.0333,   30.0, 14.0),
    # USDC($1)/AAVE($120): ratio = 1/120 â‰ˆ 0.00833 AAVE per USDC
    ("USDC/AAVE",   "univ3_3000", 0.003,  "qsv2",        0.003,  800_000,    500_000,   0.00833,  28.0, 13.0),
    # WMATIC($0.40)/AAVE($120): ratio = 0.40/120 â‰ˆ 0.00333 AAVE per WMATIC
    ("WMATIC/AAVE", "univ3_3000", 0.003,  "qsv2",        0.003,  400_000,    350_000,   0.00333,  35.0, 15.0),
    # WETH($2500)/LINK($12): ratio = 2500/12 â‰ˆ 208.3 LINK per WETH
    ("WETH/LINK",   "univ3_3000", 0.003,  "qsv2",        0.003,  600_000,    450_000,   208.3,    22.0, 10.0),
    # WETH($2500)/AAVE($120): ratio = 2500/120 â‰ˆ 20.83 AAVE per WETH
    ("WETH/AAVE",   "univ3_3000", 0.003,  "qsv2",        0.003,  700_000,    500_000,   20.83,    20.0, 10.0),
]

# Seeded PRNG so results are reproducible across runs
_RNG = _random.Random(0x4170786F)  # "Apxo" seed


def _simulate_pools(scan_no: int) -> Dict[str, List[_PoolSnapshot]]:
    """
    Generate realistic mock pool snapshots for offline testing.

    ``base_price`` in each template is the AMM ratio price:
    ``token1_normalised / token0_normalised = price_token0_usd / price_token1_usd``.

    Spreads are drawn from a half-normal distribution calibrated to
    Polygon mainnet historical observations.  The scan_no seed offset
    ensures each scan round returns slightly different spreads to model
    temporal price evolution.
    """
    raise RuntimeError(
        "SYNTHETIC_POOL_DATA_DISABLED: executable discovery must use live "
        "Polygon market data; no env flag can enable generated pool samples"
    )
    if _real_market_data_only():
        raise RuntimeError(
            "SYNTHETIC_POOL_DATA_DISABLED: REAL_MARKET_DATA_ONLY=true forbids "
            "offline/mock pool generation for executable discovery"
        )
    if not _env_bool("APEX_ALLOW_SYNTHETIC_TEST_DATA", False):
        raise RuntimeError(
            "SYNTHETIC_POOL_DATA_DISABLED: live discovery must use on-chain "
            "pool snapshots; set APEX_ALLOW_SYNTHETIC_TEST_DATA=true only in tests"
        )
    pool_map: Dict[str, List[_PoolSnapshot]] = {}
    _RNG.seed(0x4170786F + scan_no * 17)  # deterministic per scan round

    # USD prices for each token (used to compute normalised reserve sizes)
    token_usd: Dict[str, float] = {
        "USDC": 1.0, "USDT": 1.0, "DAI": 1.0,
        "WMATIC": 0.40, "WETH": 2500.0, "WBTC": 65_000.0,
        "LINK": 12.0, "AAVE": 120.0,
    }

    for tmpl in _SIM_TEMPLATES:
        (pair_key, dex_a, fee_a, dex_b, fee_b,
         tvl_a, tvl_b, base_price,
         spread_mean, spread_std) = tmpl

        sym0, sym1 = pair_key.split("/")
        price0_usd = token_usd.get(sym0, 1.0)

        # Draw a non-negative spread then add minor jitter to the base price
        raw_spread_bps = abs(_RNG.gauss(spread_mean, spread_std))
        price_jitter = _RNG.gauss(0.0, base_price * 0.001)
        price_a = max(base_price + price_jitter, base_price * 1e-6)
        price_b = price_a * (1.0 + raw_spread_bps / 10_000.0)

        # Reserves: token0 in normalised units, token1 = r0 Ã— AMM_ratio
        # (balanced pool: half TVL in each token)
        r0_a = (tvl_a / 2.0) / price0_usd
        r1_a = r0_a * price_a          # â† correct: r1 = r0 Ã— (token1/token0)

        r0_b = (tvl_b / 2.0) / price0_usd
        r1_b = r0_b * price_b

        snap_a = _PoolSnapshot(
            pool_address=f"0xSIM_{dex_a}_{pair_key.replace('/', '')}",
            dex=dex_a,
            fee=fee_a,
            sym0=sym0,
            sym1=sym1,
            reserve0=r0_a,
            reserve1=r1_a,
            price=price_a,
        )
        snap_b = _PoolSnapshot(
            pool_address=f"0xSIM_{dex_b}_{pair_key.replace('/', '')}",
            dex=dex_b,
            fee=fee_b,
            sym0=sym0,
            sym1=sym1,
            reserve0=r0_b,
            reserve1=r1_b,
            price=price_b,
        )

        existing = pool_map.get(pair_key, [])
        existing.extend([snap_a, snap_b])
        pool_map[pair_key] = existing

    return pool_map


def _cpmm_swap_out(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Constant-product swap: how much ``out`` you receive for ``amount_in``."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    eff_in = amount_in * (1.0 - fee)
    return (eff_in * reserve_out) / (reserve_in + eff_in)


def _pool_swap_out(amount_in: float, pool: "_PoolSnapshot", swap_0_to_1: bool) -> float:
    """Dispatch swap math by pool kind (CPMM for UniV3/V2/Balancer-50/50,
    StableSwap for Curve)."""
    if "balancer" in str(pool.dex).lower():
        return 0.0
    if pool.kind == "curve_ss":
        # 2-coin pairwise view of the n-coin pool: i=0 if swapping
        # token0â†’token1 (matches sym0â†’sym1 ordering), else i=1.
        i, j = (0, 1) if swap_0_to_1 else (1, 0)
        balances = [pool.reserve0, pool.reserve1]
        return _curve_get_dy(i, j, amount_in, balances, pool.amp, pool.fee)
    # CPMM default
    r_in, r_out = (pool.reserve0, pool.reserve1) if swap_0_to_1 else (pool.reserve1, pool.reserve0)
    return _cpmm_swap_out(amount_in, r_in, r_out, pool.fee)


def _quote_pool_live(
    w3: Web3,
    pool: "_PoolSnapshot",
    from_sym: str,
    to_sym: str,
    amount_in_raw: int,
) -> int:
    """Return live quoted output for one pool edge.

    V3/Algebra routes must use protocol quoters; V2-compatible routes can use
    router getAmountsOut. Curve/Balancer require pool-specific metadata and are
    intentionally left to the payload builder until their live route scorer is
    explicit.
    """
    if from_sym not in TOKENS or to_sym not in TOKENS:
        return 0
    try:
        if str(pool.dex).startswith("balancer"):
            venue, fee = "balancer_v2", None
        elif pool.dex == "curve_ss":
            venue, fee = "curve", None
        else:
            venue, fee = _venue_and_fee(pool.dex)
    except Exception:
        return 0
    venue_spec = VENUES.get(venue)
    if venue_spec is None:
        return 0
    token_in = TOKENS[from_sym].address
    token_out = TOKENS[to_sym].address
    try:
        if venue_spec.kind == "v2":
            if not venue_spec.router:
                return 0
            return _v2_live_amount_out(w3, venue_spec.router, amount_in_raw, token_in, token_out)
        if venue_spec.kind == "v3":
            if fee is None:
                return 0
            return _v3_live_amount_out(w3, amount_in_raw, token_in, token_out, int(fee))
        if venue_spec.kind == "algebra":
            return _algebra_live_amount_out(w3, amount_in_raw, token_in, token_out)
        if venue_spec.kind == "curve":
            if pool.coin0_index is None or pool.coin1_index is None:
                return 0
            if pool.sym0 == from_sym:
                coin_i, coin_j = int(pool.coin0_index), int(pool.coin1_index)
            else:
                coin_i, coin_j = int(pool.coin1_index), int(pool.coin0_index)
            quoted = _curve_live_amount_out(w3, pool.pool_address, coin_i, coin_j, amount_in_raw)
            return int(quoted[0] if isinstance(quoted, tuple) else quoted)
        if venue_spec.kind == "balancer":
            if not venue_spec.router or not pool.pool_id:
                return 0
            return _balancer_live_amount_out(
                w3,
                venue_spec.router,
                _pool_id_bytes(pool.pool_id),
                amount_in_raw,
                token_in,
                token_out,
                os.getenv("C1_TARGET") or os.getenv("EXECUTOR_ADDRESS") or token_in,
            )
    except Exception:
        return 0
    return 0


def _quote_pool_live_with_error(
    w3: Web3,
    pool: "_PoolSnapshot",
    from_sym: str,
    to_sym: str,
    amount_in_raw: int,
) -> Tuple[int, str]:
    """Return ``(amount_out, error)`` for diagnostics.

    The scanner still treats any zero output as fail-closed, but the artifact
    must explain why a route never reached payload/fork proof.
    """
    if from_sym not in TOKENS or to_sym not in TOKENS:
        return 0, "TOKEN_NOT_IN_EXECUTION_REGISTRY"
    try:
        if str(pool.dex).startswith("balancer"):
            venue, fee = "balancer_v2", None
        elif pool.dex == "curve_ss":
            venue, fee = "curve", None
        else:
            venue, fee = _venue_and_fee(pool.dex)
    except Exception as exc:  # noqa: BLE001
        return 0, f"VENUE_MAP_FAILED: {exc}"
    venue_spec = VENUES.get(venue)
    if venue_spec is None:
        return 0, f"VENUE_NOT_REGISTERED: {venue}"
    token_in = TOKENS[from_sym].address
    token_out = TOKENS[to_sym].address
    try:
        if venue_spec.kind == "v2":
            if not venue_spec.router:
                return 0, "V2_ROUTER_MISSING"
            out = _v2_live_amount_out(w3, venue_spec.router, amount_in_raw, token_in, token_out)
        elif venue_spec.kind == "v3":
            if fee is None:
                return 0, "V3_FEE_TIER_MISSING"
            out = _v3_live_amount_out(w3, amount_in_raw, token_in, token_out, int(fee))
        elif venue_spec.kind == "algebra":
            out = _algebra_live_amount_out(w3, amount_in_raw, token_in, token_out)
        elif venue_spec.kind == "curve":
            if pool.coin0_index is None or pool.coin1_index is None:
                return 0, "CURVE_COIN_INDICES_MISSING"
            if pool.sym0 == from_sym:
                coin_i, coin_j = int(pool.coin0_index), int(pool.coin1_index)
            else:
                coin_i, coin_j = int(pool.coin1_index), int(pool.coin0_index)
            quoted = _curve_live_amount_out(w3, pool.pool_address, coin_i, coin_j, amount_in_raw)
            out = int(quoted[0] if isinstance(quoted, tuple) else quoted)
        elif venue_spec.kind == "balancer":
            if not venue_spec.router:
                return 0, "BALANCER_VAULT_MISSING"
            if not pool.pool_id:
                return 0, "BALANCER_POOL_ID_MISSING"
            out = _balancer_live_amount_out(
                w3,
                venue_spec.router,
                _pool_id_bytes(pool.pool_id),
                amount_in_raw,
                token_in,
                token_out,
                os.getenv("C1_TARGET") or os.getenv("EXECUTOR_ADDRESS") or token_in,
            )
        else:
            return 0, f"VENUE_KIND_UNSUPPORTED: {venue_spec.kind}"
    except Exception as exc:  # noqa: BLE001
        return 0, f"{venue_spec.kind.upper()}_QUOTE_FAILED: {exc}"
    if out <= 0:
        return 0, "QUOTE_RETURNED_ZERO"
    return int(out), ""


def _scan_live_quoted_cycles(
    scan_no: int,
    w3: Web3,
    pool_map: Dict[str, List["_PoolSnapshot"]],
    token_prices: Dict[str, float],
    tip_optimizer: TipOptimizer,
    *,
    max_trade_size_usd: float,
    flash_loan_fee_rate: float,
    min_net_profit_usd: float,
    min_flash_loan_usd: float = 0.0,
    max_hops: int = 4,
    cycle_limit: int = 50,
    time_budget_seconds: float = 30.0,
    size_grid_usd: Optional[List[float]] = None,
    rpc_rotation: Optional[RpcRotationManager] = None,
) -> List[OpportunityRecord]:
    """Score 2-4 swap cycles from live per-leg quotes, not reserve math."""
    graph = RouteGraph(pool_map)
    try:
        latest_block = int(w3.eth.block_number)
    except Exception:
        latest_block = 0
    out: List[OpportunityRecord] = []
    evaluated = 0
    skipped_duplicate_pool_cycles = 0
    skipped_duplicate_selected_pool_cycles = 0
    skipped_below_min_flash_loan = 0
    quote_failures: Dict[str, int] = {}
    complete_quotes = 0
    quote_cache: Dict[Tuple[str, str, str, int], int] = {}
    quote_cache_hits = 0
    quote_cache_misses = 0
    quote_timeouts = 0
    pre_ranked_cycles = 0
    skipped_above_tvl_cap = 0
    near_misses: List[Dict[str, Any]] = []
    quote_failure_details: List[Dict[str, Any]] = []
    started = time.time()
    cycle_unbounded = cycle_limit <= 0
    time_unbounded = time_budget_seconds <= 0
    preferred_starts = [
        sym.strip()
        for sym in os.getenv(
            "LIVE_QUOTE_START_SYMBOLS",
            "USDCe,USDC,USDT,DAI,WMATIC,WETH,WBTC",
        ).split(",")
        if sym.strip()
    ]
    graph_tokens = list(graph.tokens)
    start_symbols = [
        sym for sym in preferred_starts if sym in graph_tokens
    ] + [
        sym for sym in sorted(graph_tokens) if sym not in preferred_starts
    ]
    allow_repeated_pool = _env_bool("LIVE_QUOTE_ALLOW_REPEATED_POOL_CYCLES", False)
    pre_rank_enabled = _env_bool("LIVE_QUOTE_PRERANK_ENABLED", True)
    pre_rank_pool_fanout = 8
    sizing_mode = os.getenv("LIVE_QUOTE_SIZING_MODE", "optimal").strip().lower()
    record_limit = int(_env_float("LIVE_QUOTE_RECORD_LIMIT", 0.0))
    record_unbounded = record_limit <= 0
    progress_every_cycles = max(1, int(_env_float("LIVE_QUOTE_PROGRESS_EVERY_CYCLES", 25.0)))
    quote_timeout_sec = max(1.0, _env_float("LIVE_QUOTE_PER_POOL_TIMEOUT_SEC", 12.0))
    quote_workers = max(1, int(_env_float("LIVE_QUOTE_WORKERS", 8.0)))
    adaptive_size_enabled = _env_bool("LIVE_QUOTE_ADAPTIVE_SIZE_ENABLED", True)
    adaptive_size_fractions = [
        float(part.strip())
        for part in os.getenv(
            "LIVE_QUOTE_ADAPTIVE_SIZE_FRACTIONS",
            "0.015625,0.03125,0.0625,0.125,0.25,0.5,0.75,1.0",
        ).split(",")
        if part.strip()
    ]
    adaptive_size_fractions = sorted({
        fraction
        for fraction in adaptive_size_fractions
        if math.isfinite(fraction) and 0.0 < fraction <= 1.0
    })
    quote_executor = ThreadPoolExecutor(max_workers=quote_workers)
    quote_error_cache: Dict[Tuple[str, str, str, int], str] = {}

    def hop_tvl_cap_bps(hop_count: int) -> float:
        if hop_count <= 2:
            return _env_float("LIVE_QUOTE_TVL_CAP_BPS_2_HOP", 1500.0)
        if hop_count == 3:
            return _env_float("LIVE_QUOTE_TVL_CAP_BPS_3_HOP", 1250.0)
        return _env_float("LIVE_QUOTE_TVL_CAP_BPS_4_HOP", 1000.0)

    def pool_tvl_usd(pool: "_PoolSnapshot") -> float:
        return _snapshot_tvl_usd(pool, token_prices)

    def directed_reserves(pool: "_PoolSnapshot", from_sym: str, to_sym: str) -> Tuple[float, float]:
        if pool.sym0 == from_sym and pool.sym1 == to_sym:
            return float(pool.reserve0), float(pool.reserve1)
        if pool.sym1 == from_sym and pool.sym0 == to_sym:
            return float(pool.reserve1), float(pool.reserve0)
        return 0.0, 0.0

    def algebraic_optimal_input_units(
        first_pool: "_PoolSnapshot",
        token_path: List[str],
        hint_pools: List["_PoolSnapshot"],
    ) -> float:
        """Route-optimal input used only for choosing live quote sizes.

        Exact coupled math is used for true two-pool CPMM cycles. Other route
        families fall back to a first-leg approximation and are still proven by
        live protocol quotes before payload/fork eligibility.
        """
        if not hint_pools or first_pool.kind not in {"cpmm", "v2", "v3", "algebra"}:
            return 0.0

        if (
            len(hint_pools) == 2
            and len(token_path) == 3
            and all(pool.kind in {"cpmm", "v2"} for pool in hint_pools)
            and token_path[0] == token_path[-1]
        ):
            reserve_in_a, reserve_out_a = directed_reserves(hint_pools[0], token_path[0], token_path[1])
            reserve_in_b, reserve_out_b = directed_reserves(hint_pools[1], token_path[1], token_path[2])
            if min(reserve_in_a, reserve_out_a, reserve_in_b, reserve_out_b) > 0.0:
                exact = two_pool_cpmm_optimal_input(
                    reserve_in_a=reserve_in_a,
                    reserve_out_a=reserve_out_a,
                    reserve_in_b=reserve_in_b,
                    reserve_out_b=reserve_out_b,
                    fee_bps_a=max(0.0, float(hint_pools[0].fee) * 10_000.0),
                    fee_bps_b=max(0.0, float(hint_pools[1].fee) * 10_000.0),
                )
                if math.isfinite(exact) and exact > 0.0:
                    return exact

        reserve_in, reserve_out = directed_reserves(first_pool, token_path[0], token_path[1])
        if reserve_in <= 0.0 or reserve_out <= 0.0:
            return 0.0
        market_price = 1.0
        for idx, pool in enumerate(hint_pools[1:], start=1):
            ratio = edge_hint_ratio(pool, token_path[idx], token_path[idx + 1])
            if ratio <= 0.0 or not math.isfinite(ratio):
                return 0.0
            market_price *= ratio
        gamma = max(0.0, 1.0 - float(first_pool.fee))
        if gamma <= 0.0 or market_price <= 0.0:
            return 0.0
        try:
            optimal = (math.sqrt(reserve_in * reserve_out * gamma * market_price) - reserve_in) / gamma
        except ValueError:
            return 0.0
        if not math.isfinite(optimal) or optimal <= 0.0:
            return 0.0
        return optimal

    def size_candidates_for_route(
        route_cap_usd: float,
        configured_sizes: List[float],
        token_path: List[str],
        hint_pools: List["_PoolSnapshot"],
        start_price: float,
    ) -> List[float]:
        if route_cap_usd < min_flash_loan_usd:
            return []
        dynamic: List[float] = []
        exact_two_pool_cpmm = (
            len(hint_pools) == 2
            and len(token_path) == 3
            and token_path[0] == token_path[-1]
            and all(pool.kind in {"cpmm", "v2"} for pool in hint_pools)
        )
        optimal_units = algebraic_optimal_input_units(hint_pools[0], token_path, hint_pools) if hint_pools else 0.0
        optimal_usd = optimal_units * start_price if optimal_units > 0.0 else 0.0
        if sizing_mode in {"optimal", "auto", "algebraic"} and optimal_usd > 0.0:
            clamped = min(optimal_usd, route_cap_usd, max_trade_size_usd)
            dynamic.extend([
                clamped * 0.50,
                clamped * 0.75,
                clamped,
                min(route_cap_usd, clamped * 1.10),
                min(route_cap_usd, clamped * 1.25),
            ])
            if adaptive_size_enabled and not exact_two_pool_cpmm:
                dynamic.extend([
                    route_cap_usd * fraction
                    for fraction in adaptive_size_fractions
                ])
        elif sizing_mode in {"fixed", "grid"}:
            dynamic.extend(configured_sizes)
        else:
            # Fallback only when algebra finds no structural edge. These are
            # derived from the route cap, not a global arbitrary ladder.
            fractions = adaptive_size_fractions if adaptive_size_enabled else [0.25, 0.50, 0.75, 1.0]
            dynamic.extend([
                route_cap_usd * fraction
                for fraction in fractions
            ])
        if adaptive_size_enabled and sizing_mode not in {"fixed", "grid"}:
            # Mixed V2/V3/Balancer/Curve paths are ultimately proven by live
            # protocol quotes, not the approximate algebraic hint. Always keep
            # the route cap probe so the scanner does not miss a route whose
            # profitable region sits far above the hint. Do not inject the
            # minimum as a synthetic trade size; it is only a lower bound.
            dynamic.append(min(route_cap_usd, max_trade_size_usd))
        combined = [
            float(size)
            for size in dynamic
            if (
                float(size) > 0.0
                and float(size) >= min_flash_loan_usd
                and float(size) <= max_trade_size_usd
                and float(size) <= route_cap_usd
            )
        ]
        if not combined and min_flash_loan_usd <= route_cap_usd:
            combined.append(min(route_cap_usd, max_trade_size_usd))
        return sorted({round(size, 6) for size in combined})

    def quote_cached(pool: "_PoolSnapshot", from_sym: str, to_sym: str, amount_raw: int) -> int:
        nonlocal quote_cache_hits, quote_cache_misses, quote_timeouts
        cache_id = str(pool.pool_id or pool.pool_address).lower()
        key = (cache_id, from_sym, to_sym, int(amount_raw))
        cached = quote_cache.get(key)
        if cached is not None:
            quote_cache_hits += 1
            return cached
        quote_cache_misses += 1
        attempts = min(3, len(rpc_rotation.urls)) if rpc_rotation is not None else 1
        quoted = 0
        error = ""
        for attempt in range(max(1, attempts)):
            quote_w3 = w3
            quote_url = ""
            if rpc_rotation is not None:
                try:
                    quote_w3, quote_url = rpc_rotation.get_web3()
                except RpcRotationError as exc:
                    error = f"RPC_ROTATION_EXHAUSTED: {exc}"
                    break
            future = quote_executor.submit(_quote_pool_live_with_error, quote_w3, pool, from_sym, to_sym, amount_raw)
            try:
                quoted, error = future.result(timeout=quote_timeout_sec)
            except TimeoutError:
                quote_timeouts += 1
                future.cancel()
                quoted = 0
                error = f"QUOTE_TIMEOUT_AFTER_{quote_timeout_sec:.1f}s"
            except Exception as exc:  # noqa: BLE001
                quoted = 0
                error = f"QUOTE_WORKER_EXCEPTION: {exc}"
            if quoted > 0:
                if rpc_rotation is not None and quote_url:
                    rpc_rotation.mark_success(quote_url)
                break
            if rpc_rotation is not None and quote_url:
                rpc_rotation.mark_failure(quote_url, error)
            if not _is_retryable_rpc_error(error) or attempt >= attempts - 1:
                break
        quote_cache[key] = quoted
        quote_error_cache[key] = error
        return quoted

    def quote_error_for(pool: "_PoolSnapshot", from_sym: str, to_sym: str, amount_raw: int) -> str:
        cache_id = str(pool.pool_id or pool.pool_address).lower()
        return quote_error_cache.get((cache_id, from_sym, to_sym, int(amount_raw)), "")

    def edge_hint_ratio(pool: "_PoolSnapshot", from_sym: str, to_sym: str) -> float:
        """Cheap ordering hint only; live quotes remain the proof source."""
        if pool.reserve0 <= 0.0 or pool.reserve1 <= 0.0:
            return 0.0
        if pool.sym0 == from_sym and pool.sym1 == to_sym:
            return (pool.reserve1 / pool.reserve0) * max(0.0, 1.0 - float(pool.fee))
        if pool.sym1 == from_sym and pool.sym0 == to_sym:
            return (pool.reserve0 / pool.reserve1) * max(0.0, 1.0 - float(pool.fee))
        return 0.0

    def cycle_hint_score(token_path: List[str]) -> float:
        """Rank likely-positive cycles before spending live quote RPC calls."""
        score = 1.0
        liquidity_floor = float("inf")
        for idx in range(len(token_path) - 1):
            from_sym = token_path[idx]
            to_sym = token_path[idx + 1]
            pools = graph.pools_for_edge(from_sym, to_sym)
            if not pools:
                return float("-inf")
            ranked = sorted(
                pools,
                key=lambda p: edge_hint_ratio(p, from_sym, to_sym),
                reverse=True,
            )[:pre_rank_pool_fanout]
            best_ratio = max((edge_hint_ratio(p, from_sym, to_sym) for p in ranked), default=0.0)
            if best_ratio <= 0.0:
                return float("-inf")
            score *= best_ratio
            liquidity_floor = min(
                liquidity_floor,
                max(float(getattr(p, "tvl_usd", 0.0) or 0.0) for p in ranked),
            )
        hop_count = max(1, len(token_path) - 1)
        liquidity_bonus = math.log10(max(liquidity_floor, 1.0)) / 100.0
        hop_penalty = 1.0 + max(0, hop_count - 2) * 0.0025
        return ((score - 1.0) / hop_penalty) + liquidity_bonus

    def write_progress(status: str = "scanning") -> None:
        near_misses.sort(key=lambda item: float(item.get("net_profit_usd", -1e18)), reverse=True)
        _write_live_quote_diagnostics({
            "status": status,
            "generated_at": time.time(),
            "scan_no": scan_no,
            "cycle_limit": "unbounded" if cycle_unbounded else cycle_limit,
            "time_budget_seconds": "unbounded" if time_unbounded else time_budget_seconds,
            "elapsed_seconds": round(time.time() - started, 3),
            "cycles_evaluated": evaluated,
            "skipped_duplicate_pool_cycles": skipped_duplicate_pool_cycles,
            "skipped_duplicate_selected_pool_cycles": skipped_duplicate_selected_pool_cycles,
            "skipped_below_min_flash_loan": skipped_below_min_flash_loan,
            "skipped_above_tvl_cap": skipped_above_tvl_cap,
            "complete_quote_paths": complete_quotes,
            "profitable_records": len(out),
            "record_limit": "unbounded" if record_unbounded else record_limit,
            "min_net_profit_usd": min_net_profit_usd,
            "quote_cache": {
                "enabled": True,
                "entries": len(quote_cache),
                "hits": quote_cache_hits,
                "misses": quote_cache_misses,
                "timeouts": quote_timeouts,
                "per_pool_timeout_sec": quote_timeout_sec,
                "workers": quote_workers,
            },
            "pre_rank": {
                "enabled": pre_rank_enabled,
                "cycles_ranked": pre_ranked_cycles,
                "pool_fanout": pre_rank_pool_fanout,
            },
            "sizing": {
                "mode": sizing_mode,
                "policy": "opportunity_driven_optimal_flashloan_sizing",
                "configured_max_flashloan_cap_usd": max_trade_size_usd,
                "fallback_grid_usd": size_grid_usd or [],
                "adaptive_size_enabled": adaptive_size_enabled,
                "adaptive_size_fractions": adaptive_size_fractions,
            },
            "quote_failure_summary": dict(sorted(quote_failures.items())),
            "quote_failure_details": quote_failure_details[:50],
            "top_near_misses": near_misses[:50],
        })

    def finish() -> List[OpportunityRecord]:
        write_progress("complete")
        quote_executor.shutdown(wait=True, cancel_futures=True)
        return sorted(out, key=lambda rec: rec.e_profit, reverse=True)

    for start_sym in start_symbols:
        if not time_unbounded and time.time() - started >= time_budget_seconds:
            return finish()
        if start_sym not in TOKENS:
            continue
        start_price = float(token_prices.get(start_sym, 0.0) or 0.0)
        if start_price <= 0.0:
            continue
        start_decimals = TOKENS[start_sym].decimals
        configured_sizes_usd = [
            float(size)
            for size in (size_grid_usd or [max_trade_size_usd])
            if (
                float(size) > 0.0
                and float(size) <= max_trade_size_usd
                and float(size) >= min_flash_loan_usd
            )
        ] or [max_trade_size_usd]
        if size_grid_usd:
            skipped_below_min_flash_loan += sum(
                1
                for size in size_grid_usd
                if float(size) > 0.0 and float(size) < min_flash_loan_usd
            )
        cycles = graph.enumerate_cycles(start_sym, min_hops=2, max_hops=max_hops)
        if pre_rank_enabled and cycles:
            cycles = sorted(cycles, key=cycle_hint_score, reverse=True)
            pre_ranked_cycles += len(cycles)
        for token_path in cycles:
            if (not cycle_unbounded and evaluated >= cycle_limit) or (
                not time_unbounded and time.time() - started >= time_budget_seconds
            ):
                return finish()
            evaluated += 1
            if evaluated == 1 or evaluated % progress_every_cycles == 0:
                write_progress("scanning")
            if not allow_repeated_pool:
                duplicate_pool = False
                selected_pool_ids: List[str] = []
                for idx in range(len(token_path) - 1):
                    pools = graph.pools_for_edge(token_path[idx], token_path[idx + 1])
                    if not pools:
                        continue
                    pool_key = str(pools[0].pool_address).lower()
                    if pool_key in selected_pool_ids:
                        duplicate_pool = True
                        break
                    selected_pool_ids.append(pool_key)
                if duplicate_pool:
                    skipped_duplicate_pool_cycles += 1
                    continue
            hop_count = len(token_path) - 1
            hint_pools: List[_PoolSnapshot] = []
            for idx in range(hop_count):
                from_sym = token_path[idx]
                to_sym = token_path[idx + 1]
                pools = graph.pools_for_edge(from_sym, to_sym)
                if not pools:
                    hint_pools = []
                    break
                hint_pools.append(
                    max(
                        pools,
                        key=lambda p: (
                            edge_hint_ratio(p, from_sym, to_sym),
                            pool_tvl_usd(p),
                        ),
                    )
                )
            hint_tvl_values = [pool_tvl_usd(pool) for pool in hint_pools]
            route_hint_weakest_tvl_usd = min(hint_tvl_values) if hint_tvl_values else 0.0
            route_cap_bps = hop_tvl_cap_bps(hop_count)
            route_hint_cap_usd = min(
                max_trade_size_usd,
                route_hint_weakest_tvl_usd * route_cap_bps / 10_000.0,
            )
            sizes_usd = size_candidates_for_route(
                route_hint_cap_usd,
                configured_sizes_usd,
                token_path,
                hint_pools,
                start_price,
            )
            if not sizes_usd:
                continue
            for size_usd in sizes_usd:
                amount_in = size_usd / start_price
                amount_in_raw = max(1, int(amount_in * (10 ** start_decimals)))
                current_raw = amount_in_raw
                pools_used: List[str] = []
                selected_pools_used: List[_PoolSnapshot] = []
                dexes_used: List[str] = []
                swap_dirs: List[bool] = []
                pool_ids_used: List[str | None] = []
                curve_coin_indices_used: List[List[int] | None] = []
                leg_amounts_in: List[float] = []
                leg_amounts_out: List[float] = []
                failed = False
                for idx in range(len(token_path) - 1):
                    from_sym = token_path[idx]
                    to_sym = token_path[idx + 1]
                    if from_sym not in TOKENS or to_sym not in TOKENS:
                        failed = True
                        break
                    pools = graph.pools_for_edge(from_sym, to_sym)
                    best_pool: Optional[_PoolSnapshot] = None
                    best_out = 0
                    ranked_pools = sorted(
                        pools,
                        key=lambda p: (
                            edge_hint_ratio(p, from_sym, to_sym),
                            pool_tvl_usd(p),
                        ),
                        reverse=True,
                    )[:pre_rank_pool_fanout]
                    for pool in ranked_pools:
                        quote = quote_cached(pool, from_sym, to_sym, current_raw)
                        if quote > best_out:
                            best_out = quote
                            best_pool = pool
                    if best_pool is None or best_out <= 0:
                        failure_key = f"{from_sym}->{to_sym}"
                        quote_failures[failure_key] = quote_failures.get(failure_key, 0) + 1
                        quote_failure_details.append(
                            {
                                "route": "->".join(token_path),
                                "leg": idx,
                                "from": from_sym,
                                "to": to_sym,
                                "amount_in_raw": current_raw,
                                "amount_in_units": current_raw / float(10 ** TOKENS[from_sym].decimals),
                                "candidate_pools": [
                                    {
                                        "dex": pool.dex,
                                        "kind": pool.kind,
                                        "pool": pool.pool_address,
                                        "pool_id": pool.pool_id,
                                        "tvl_usd": round(pool_tvl_usd(pool), 6),
                                        "error": quote_error_for(pool, from_sym, to_sym, current_raw) or "NO_POSITIVE_QUOTE",
                                    }
                                    for pool in ranked_pools
                                ],
                            }
                        )
                        if len(quote_failure_details) > 200:
                            del quote_failure_details[100:]
                        failed = True
                        break
                    leg_amounts_in.append(current_raw / float(10 ** TOKENS[from_sym].decimals))
                    leg_amounts_out.append(best_out / float(10 ** TOKENS[to_sym].decimals))
                    pools_used.append(best_pool.pool_address)
                    selected_pools_used.append(best_pool)
                    dexes_used.append(best_pool.dex)
                    swap_0_to_1 = best_pool.sym0 == from_sym
                    swap_dirs.append(swap_0_to_1)
                    pool_ids_used.append(best_pool.pool_id)
                    if best_pool.dex == "curve_ss":
                        if best_pool.coin0_index is None or best_pool.coin1_index is None:
                            failed = True
                            break
                        curve_coin_indices_used.append(
                            [int(best_pool.coin0_index), int(best_pool.coin1_index)]
                            if swap_0_to_1
                            else [int(best_pool.coin1_index), int(best_pool.coin0_index)]
                        )
                    else:
                        curve_coin_indices_used.append(None)
                    current_raw = best_out
                if failed:
                    continue
                if not allow_repeated_pool:
                    selected_keys = [
                        str(pool_id or pool_addr).lower()
                        for pool_id, pool_addr in zip(pool_ids_used, pools_used)
                    ]
                    if len(selected_keys) != len(set(selected_keys)):
                        skipped_duplicate_selected_pool_cycles += 1
                        continue
                selected_weakest_tvl_usd = min(
                    (pool_tvl_usd(pool) for pool in selected_pools_used),
                    default=route_hint_weakest_tvl_usd,
                )
                selected_cap_usd = min(
                    max_trade_size_usd,
                    selected_weakest_tvl_usd * route_cap_bps / 10_000.0,
                )
                if size_usd > selected_cap_usd + 1e-9:
                    skipped_above_tvl_cap += 1
                    continue
                complete_quotes += 1
                final_amount = current_raw / float(10 ** start_decimals)
                leg_price = _leg_price_invariant(
                    leg_amounts_in=leg_amounts_in,
                    leg_amounts_out=leg_amounts_out,
                    quote_block=latest_block,
                )
                if _leg_price_has_structural_failure(leg_price):
                    near_misses.append({
                        "route": "->".join(token_path),
                        "size_usd": round(size_usd, 4),
                        "hop_count": hop_count,
                        "reason": leg_price["leg_price_invariant_reason"],
                        "buy_leg1_price": leg_price["buy_leg1_price"],
                        "sell_leg2_price": leg_price["sell_leg2_price"],
                        "leg_price_executable_spread_bps": leg_price["leg_price_executable_spread_bps"],
                        "dexes": dexes_used,
                        "pools": pools_used,
                        "pool_ids": pool_ids_used,
                        "curve_coin_indices": curve_coin_indices_used,
                        "weakest_pool_tvl_usd": round(selected_weakest_tvl_usd, 4),
                        "route_tvl_cap_bps": round(route_cap_bps, 4),
                        "route_size_cap_usd": round(selected_cap_usd, 4),
                    })
                    if len(near_misses) > 200:
                        near_misses.sort(key=lambda item: float(item.get("net_profit_usd", -1e18)), reverse=True)
                        del near_misses[100:]
                    continue
                gross_profit_usd = (final_amount - amount_in) * start_price
                flash_fee_usd = size_usd * flash_loan_fee_rate
                eip1559 = tip_optimizer.build_eip1559_params(max(gross_profit_usd - flash_fee_usd, 0.01))
                hop_count = len(token_path) - 1
                gas_cost_usd = eip1559["gas_cost_usd"] * max(1.0, 1.0 + (hop_count - 2) * 0.5)
                net_profit_usd = gross_profit_usd - flash_fee_usd - gas_cost_usd
                if net_profit_usd < min_net_profit_usd:
                    near_misses.append({
                        "route": "->".join(token_path),
                        "size_usd": round(size_usd, 4),
                        "hop_count": hop_count,
                        "gross_profit_usd": round(gross_profit_usd, 8),
                        "flash_fee_usd": round(flash_fee_usd, 8),
                        "gas_cost_usd": round(gas_cost_usd, 8),
                        "net_profit_usd": round(net_profit_usd, 8),
                        "buy_leg1_price": leg_price["buy_leg1_price"],
                        "sell_leg2_price": leg_price["sell_leg2_price"],
                        "leg_price_executable_spread_bps": leg_price["leg_price_executable_spread_bps"],
                        "leg_price_invariant_status": leg_price["leg_price_invariant_status"],
                        "leg_price_invariant_reason": leg_price["leg_price_invariant_reason"],
                        "dexes": dexes_used,
                        "pools": pools_used,
                        "pool_ids": pool_ids_used,
                        "curve_coin_indices": curve_coin_indices_used,
                        "weakest_pool_tvl_usd": round(selected_weakest_tvl_usd, 4),
                        "route_tvl_cap_bps": round(route_cap_bps, 4),
                        "route_size_cap_usd": round(selected_cap_usd, 4),
                    })
                    if len(near_misses) > 200:
                        near_misses.sort(key=lambda item: float(item.get("net_profit_usd", -1e18)), reverse=True)
                        del near_misses[100:]
                    continue
                p_fill = eip1559["p_fill"]
                route_label = "→".join(token_path)
                out.append(
                    OpportunityRecord(
                        scan_no=scan_no,
                        timestamp=time.time(),
                        pair=route_label,
                        buy_dex=dexes_used[0] if dexes_used else "unknown",
                        sell_dex=dexes_used[-1] if dexes_used else "unknown",
                        buy_pool=pools_used[0] if pools_used else "",
                        sell_pool=pools_used[-1] if pools_used else "",
                        buy_price_usdc=0.0,
                        sell_price_usdc=0.0,
                        spot_spread_bps=round(10_000.0 * gross_profit_usd / max(size_usd, 1.0), 4),
                        executable_spread_bps=round(10_000.0 * gross_profit_usd / max(size_usd, 1.0), 4),
                        raw_spread_bps=round(10_000.0 * gross_profit_usd / max(size_usd, 1.0), 4),
                        flash_size_usd=round(size_usd, 2),
                        trade_size_usd=round(size_usd, 2),
                        gross_profit_usd=round(gross_profit_usd, 4),
                        slippage_cost_usd=0.0,
                        flash_fee_usd=round(flash_fee_usd, 4),
                        gas_cost_usd=round(gas_cost_usd, 4),
                        expected_net_edge=round(net_profit_usd, 4),
                        p_fill=round(p_fill, 4),
                        e_profit=round(net_profit_usd * p_fill, 4),
                        profitable=True,
                        hop_count=hop_count,
                        route_tokens="->".join(token_path),
                        route_pools="->".join(pools_used),
                        route_dexes="->".join(dexes_used),
                        route_id="liveq_" + str(abs(hash((tuple(token_path), tuple(pools_used), size_usd))))[0:12],
                        route_leg_amounts_in=json.dumps(leg_amounts_in),
                        route_leg_amounts_out=json.dumps(leg_amounts_out),
                        route_swap_0_to_1=json.dumps(swap_dirs),
                        route_pool_ids=json.dumps(pool_ids_used),
                        route_curve_coin_indices=json.dumps(curve_coin_indices_used),
                        **leg_price,
                        weakest_pool_tvl_usd=round(selected_weakest_tvl_usd, 4),
                        route_tvl_cap_bps=round(route_cap_bps, 4),
                        route_size_cap_usd=round(selected_cap_usd, 4),
                    )
                )
                if not record_unbounded and len(out) >= record_limit:
                    return finish()
    return finish()


def _best_pool_for_swap(
    pools: List["_PoolSnapshot"], from_sym: str
) -> Optional[Tuple["_PoolSnapshot", bool]]:
    """Return ``(pool, swap0to1)`` flag for the deepest pool in this pair."""
    if not pools:
        return None
    deepest = max(pools, key=lambda p: p.reserve0 + p.reserve1)
    swap_0_to_1 = (deepest.sym0 == from_sym)
    return deepest, swap_0_to_1


def _select_cycle_extrema(
    pools: List["_PoolSnapshot"],
) -> Optional[Tuple["_PoolSnapshot", "_PoolSnapshot"]]:
    """Return ``(best_buy_pool, best_sell_pool)`` for a same-pair pool list.

    For each scan cycle and each token pair, the maximum net profit comes from
    using exactly these two endpoints:

    * **best_buy_pool** â€” pool with the *highest* token1-per-token0 spot price
      (``reserve1 / reserve0``).  Buying token1 here is cheapest: you receive
      the most token1 per unit of token0 spent.

    * **best_sell_pool** â€” pool with the *lowest* token1-per-token0 spot price.
      Selling token1 here is most profitable: the pool values token1 most
      highly relative to token0, so you receive the most token0 per token1
      sold.

    The cross-pool spread ``best_buy_pool.price âˆ’ best_sell_pool.price`` is
    always â‰¥ the spread of any other ``(i, j)`` combination from the same
    list, so testing only this pair yields the maximum possible raw edge
    without iterating O(NÂ²) combinations.

    Only constant-product (CPMM) pools with positive reserves are considered.
    Curve StableSwap pools (``kind == "curve_ss"``) are excluded because their
    apparent imbalance is not a price gap and mis-pricing them would produce
    fake spreads.

    Returns ``None`` when fewer than two *distinct* eligible pools survive,
    which means no cross-pool arbitrage is available for this pair in this
    cycle.
    """
    eligible = [
        p for p in pools
        if p.kind == "cpmm" and p.reserve0 > 0 and p.reserve1 > 0
    ]
    if len(eligible) < 2:
        return None

    # Most token1 per token0 â†’ cheapest place to buy token1
    best_buy = max(eligible, key=lambda p: p.price)
    # Least token1 per token0 â†’ best place to sell token1 (most token0 back)
    best_sell = min(eligible, key=lambda p: p.price)

    if best_buy.pool_address == best_sell.pool_address:
        # Only one pool; no cross-pool arb possible
        return None

    return best_buy, best_sell


def _triangular_profit_in_token_a(
    x_in_a: float,
    leg_ab: Tuple["_PoolSnapshot", bool],
    leg_bc: Tuple["_PoolSnapshot", bool],
    leg_ca: Tuple["_PoolSnapshot", bool],
) -> float:
    """Simulate Aâ†’Bâ†’Câ†’A and return token-A delta (negative = loss)."""
    p, dir01 = leg_ab
    y_b = _pool_swap_out(x_in_a, p, dir01)

    p, dir01 = leg_bc
    z_c = _pool_swap_out(y_b, p, dir01)

    p, dir01 = leg_ca
    x_out_a = _pool_swap_out(z_c, p, dir01)

    return x_out_a - x_in_a


def _scan_triangular_cycles(
    scan_no: int,
    pool_map: Dict[str, List["_PoolSnapshot"]],
    token_prices: Dict[str, float],
    tip_optimizer: "TipOptimizer",
    max_trade_size_usd: float,
    flash_loan_fee_rate: float,
    min_net_profit_usd: float,
) -> List[OpportunityRecord]:
    """Search every Aâ†’Bâ†’Câ†’A cycle for owner-positive net profit.

    Uses a 24-point geometric grid from $50 to ``max_trade_size_usd``
    over the input principal in token A. Picks the size that maximises
    ranking score after owner-paid submission gas. Emits a
    record only when net profit â‰¥ ``min_net_profit_usd``.
    """
    out: List[OpportunityRecord] = []
    owner_profit_buffer_usd = max(
        0.0,
        _env_float("OWNER_PROFIT_BUFFER_USD", _env_float("MEV_BUFFER_USD", 0.0)),
    )
    syms = sorted(_TOKENS.keys())

    # Pre-index: pool list by frozenset(sym0, sym1)
    by_pair: Dict[frozenset, List["_PoolSnapshot"]] = {
        frozenset(k.split("/")): v for k, v in pool_map.items()
    }

    grid = [50.0 * (max_trade_size_usd / 50.0) ** (i / 23.0) for i in range(24)]

    for a, b, c in itertools.combinations(syms, 3):
        # All 3 legs must have at least one surviving pool
        pools_ab = by_pair.get(frozenset((a, b)))
        pools_bc = by_pair.get(frozenset((b, c)))
        pools_ca = by_pair.get(frozenset((c, a)))
        if not (pools_ab and pools_bc and pools_ca):
            continue

        # Try both rotation directions: Aâ†’Bâ†’Câ†’A and Aâ†’Câ†’Bâ†’A
        for cycle in ((a, b, c), (a, c, b)):
            t0, t1, t2 = cycle
            leg01 = _best_pool_for_swap(by_pair[frozenset((t0, t1))], t0)
            leg12 = _best_pool_for_swap(by_pair[frozenset((t1, t2))], t1)
            leg20 = _best_pool_for_swap(by_pair[frozenset((t2, t0))], t2)
            if not (leg01 and leg12 and leg20):
                continue

            price_t0_usd = token_prices.get(t0, 0.0)
            if price_t0_usd <= 0:
                continue

            best_net = -1e18
            best_ranking_edge = -1e18
            best_size_usd = 0.0
            best_gross_usd = 0.0
            for size_usd in grid:
                x_in_a = size_usd / price_t0_usd
                delta_a = _triangular_profit_in_token_a(x_in_a, leg01, leg12, leg20)
                gross_usd = delta_a * price_t0_usd
                flash_fee = size_usd * flash_loan_fee_rate
                token_net = gross_usd - flash_fee
                eip1559 = tip_optimizer.build_eip1559_params(max(token_net, 0.01))
                # Triangular costs ~3 swaps vs 2; charge ~1.5x gas
                gas_cost = eip1559["gas_cost_usd"] * 1.5
                ranking_edge = token_net - gas_cost - owner_profit_buffer_usd
                if ranking_edge > best_ranking_edge:
                    best_net = token_net
                    best_ranking_edge = ranking_edge
                    best_size_usd = size_usd
                    best_gross_usd = gross_usd

            if best_ranking_edge < min_net_profit_usd:
                continue

            p01, d01 = leg01
            p12, d12 = leg12
            p20, d20 = leg20
            amount0 = best_size_usd / price_t0_usd
            amount1 = _pool_swap_out(amount0, p01, d01)
            amount2 = _pool_swap_out(amount1, p12, d12)
            amount3 = _pool_swap_out(amount2, p20, d20)
            leg_amounts_in = [amount0, amount1, amount2]
            leg_amounts_out = [amount1, amount2, amount3]
            leg_price = _leg_price_invariant(
                leg_amounts_in=leg_amounts_in,
                leg_amounts_out=leg_amounts_out,
            )
            if _leg_price_has_structural_failure(leg_price):
                continue

            eip1559 = tip_optimizer.build_eip1559_params(max(best_net, 0.01))
            gas_cost = eip1559["gas_cost_usd"] * 1.5
            p_fill = eip1559["p_fill"]
            ranking_edge = best_net - gas_cost - owner_profit_buffer_usd
            flash_fee = best_size_usd * flash_loan_fee_rate
            cycle_label = f"{t0}->{t1}->{t2}->{t0}"
            dex_chain = "->".join(p[0].dex for p in (leg01, leg12, leg20))
            out.append(OpportunityRecord(
                scan_no=scan_no,
                timestamp=time.time(),
                pair=cycle_label,
                buy_dex=dex_chain,
                sell_dex="triangular",
                buy_pool=leg01[0].pool_address,
                sell_pool=leg20[0].pool_address,
                buy_price_usdc=0.0,
                sell_price_usdc=0.0,
                spot_spread_bps=round(10_000.0 * best_gross_usd / max(best_size_usd, 1.0), 4),
                executable_spread_bps=round(10_000.0 * best_gross_usd / max(best_size_usd, 1.0), 4),
                raw_spread_bps=round(10_000.0 * best_gross_usd / max(best_size_usd, 1.0), 4),
                flash_size_usd=round(best_size_usd, 2),
                trade_size_usd=round(best_size_usd, 2),
                gross_profit_usd=round(best_gross_usd, 4),
                slippage_cost_usd=0.0,  # already netted into gross via CPMM math
                flash_fee_usd=round(flash_fee, 4),
                gas_cost_usd=round(gas_cost, 4),
                expected_net_edge=round(best_net, 4),
                p_fill=round(p_fill, 4),
                e_profit=round(ranking_edge * p_fill if ranking_edge > 0 else 0.0, 4),
                profitable=(ranking_edge >= min_net_profit_usd),
                hop_count=3,
                route_tokens=cycle_label,
                route_pools="->".join(p.pool_address for p, _ in (leg01, leg12, leg20)),
                route_dexes=dex_chain,
                route_leg_amounts_in=json.dumps(leg_amounts_in),
                route_leg_amounts_out=json.dumps(leg_amounts_out),
                route_swap_0_to_1=json.dumps([d01, d12, d20]),
                route_pool_ids=json.dumps([None, None, None]),
                route_curve_coin_indices=json.dumps([None, None, None]),
                **leg_price,
            ))
    return out


_FLASH_LOAN_PROVIDERS: Dict[str, float] = {
    "balancer": 0.0,        # Balancer V2 vault flash loans â€” no fee
    "aave_v3": 0.0005,      # Aave V3 - 5 bps
    "uniswap_v3": 0.0,      # UniV3 flash via callback â€” only the pool fee
}


def _resolve_flash_loan_fee_rate(provider: Optional[str]) -> float:
    fee_bps = os.getenv("FLASH_LOAN_FEE_BPS")
    if fee_bps not in (None, ""):
        return float(fee_bps) / 10_000.0
    name = (provider or os.getenv("FLASH_LOAN_PROVIDER", "balancer")).lower()
    if name not in _FLASH_LOAN_PROVIDERS:
        raise ValueError(f"unsupported flashloan provider {name!r}; all execution capital must come from a flashloan provider")
    return _FLASH_LOAN_PROVIDERS[name]


async def run_live_opportunity_scan(
    rpc_url: Optional[str] = None,
    target_count: int = 100,
    scan_interval_sec: float = 2.0,
    output_csv: Optional[str] = None,
    trade_size_usd: float = 100_000.0,
    flash_loan_provider: Optional[str] = None,
    min_pool_tvl_usd: float = 5_000.0,
    max_price_dev: float = 0.05,
    min_net_profit_usd: float = 2.0,
    enable_triangular: bool = True,
    enable_expanded_scan: bool = True,
    expanded_max_hops: int = 4,
    max_scans: Optional[int] = None,
) -> List[OpportunityRecord]:
    """
    Scan real Polygon DEX pools and record ``target_count`` opportunity
    observations.  For each cross-DEX price discrepancy the following
    metrics are logged:

    * **expected_net_edge** â€“ owner net USD profit after slippage, DEX fees,
      flash-loan fee, and wallet-paid submission gas.
    * **p_fill** â€“ logistic P(inclusion in the next block) at the
      EIP-1559 tip that maximises E[profit].
    * **E[profit]** â€“ ``p_fill x owner_net_after_gas`` (0 when edge <= 0).

    Raises
    ------
    ConnectionError
        When the Polygon RPC endpoint cannot be reached after 3 attempts.
        Simulation fallback is intentionally not provided â€” live data is
        mandatory.  Set ``POLYGON_RPC`` (or ``POLYGON_HTTP`` /
        ``ALCHEMY_HTTP_1``) to a reachable Polygon mainnet endpoint.
    """
    _assert_real_market_data_only_policy()
    initial_urls = collect_rpc_urls(chain_id=137)
    if rpc_url:
        initial_urls = [rpc_url, *[url for url in initial_urls if url != rpc_url]]
    rpc_rotation = RpcRotationManager(initial_urls, chain_id=137, timeout_s=10.0)
    try:
        w3, rpc = rpc_rotation.get_web3()
    except RpcRotationError as exc:
        raise ConnectionError(
            "Cannot reach any healthy Polygon RPC. Configure at least one "
            "reachable chain-137 endpoint in POLYGON_RPC / POLYGON_RPC_URL / "
            "PRIVATE_RPC_URL."
        ) from exc
    logger.info("Connected to Polygon RPC rotation leader: %s", rpc[:60] + "...")
    logger.info("Connected. Block #%d", w3.eth.block_number)

    sentinel = SlippageSentinel()
    gas_oracle = GasOracle(rpc_url=rpc, w3=w3)
    flash_loan_fee_rate = _resolve_flash_loan_fee_rate(flash_loan_provider)
    min_flash_loan_usd = _env_float("MIN_FLASH_LOAN_USD", 1_000.0)
    max_flash_loan_usd = _env_float("MAX_FLASH_LOAN_USD", 100_000.0)
    max_flash_tvl_fraction = _env_float("MAX_FLASH_TVL_FRACTION", 0.15)
    flash_size_scan_fractions = _env_float_list(
        "FLASH_SIZE_SCAN_FRACTIONS",
        [0.10],
    )
    logger.info(
        "Flash-loan provider: %s (fee=%.2f bps, min=$%.0f, max=$%.0f, tvl_fraction=%.2f)",
        (flash_loan_provider or os.getenv("FLASH_LOAN_PROVIDER", "balancer")),
        flash_loan_fee_rate * 10_000.0,
        min_flash_loan_usd,
        max_flash_loan_usd,
        max_flash_tvl_fraction,
    )

    records: List[OpportunityRecord] = []
    scan_no = 0
    unbounded_target = target_count <= 0
    target_label = "unbounded" if unbounded_target else str(target_count)

    logger.info(
        "Starting live scan: target=%s opportunities, sizing=opportunity-driven, max_flashloan_cap=$%.0f",
        target_label,
        trade_size_usd,
    )

    loop = asyncio.get_running_loop()

    while unbounded_target or len(records) < target_count:
        if max_scans is not None and scan_no >= max_scans:
            logger.info(
                "Reached max_scans=%d with %d/%s records - rotating cycle.",
                max_scans, len(records), target_label,
            )
            break
        scan_no += 1
        scan_start = time.time()

        # Refresh live gas snapshot each scan round
        gas_oracle.invalidate()
        gas_snap = await loop.run_in_executor(None, gas_oracle.get_snapshot)
        tip_optimizer = TipOptimizer(gas_snap, gas_units=_GAS_UNITS, chain="polygon")

        # Discover live on-chain pools
        pool_map = await loop.run_in_executor(None, _discover_pools, w3)
        raw_pair_count = len(pool_map)
        raw_pool_count = sum(len(pools) for pools in pool_map.values())
        token_prices, price_report = _derive_token_prices_with_report(pool_map)
        # Apply liquidity + price-sanity filters before scoring so we never
        # rank stale single-tick UniV3 pools or dust venues. Legacy same-pair
        # spread logic needs two surviving venues per pair; multi-hop graph
        # routing only needs one liquid venue per edge.
        graph_pool_map, graph_price_report = _filter_pool_universe_with_report(
            pool_map, token_prices,
            min_tvl_usd=min_pool_tvl_usd,
            max_price_dev=max_price_dev,
            price_report=price_report,
            require_multiple_venues=False,
        )
        pair_pool_map, _pair_price_report = _filter_pool_universe_with_report(
            pool_map, token_prices,
            min_tvl_usd=min_pool_tvl_usd,
            max_price_dev=max_price_dev,
            price_report=copy.deepcopy(price_report),
            require_multiple_venues=True,
        )
        _write_price_discovery_report(graph_price_report)

        mode_tag = "LIVE"
        logger.info(
            "[%s] Scan #%d: %d graph edges, %d pair-arb pairs, %d raw pairs, %d raw pools, %d/%d tokens priced, %d pools quarantined (%.1fs). Records so far: %d/%s",
            mode_tag,
            scan_no,
            len(graph_pool_map),
            len(pair_pool_map),
            raw_pair_count,
            raw_pool_count,
            len(graph_price_report.priced_tokens),
            len(graph_price_report.discovered_tokens),
            len(graph_price_report.quarantined_pools),
            time.time() - scan_start,
            len(records),
            target_label,
        )

        if _env_bool("LEGACY_PAIR_SCAN_ENABLED", True):
            for pair_key, pools in sorted(pair_pool_map.items()):
                extrema = _select_cycle_extrema(pools)
                if extrema is None:
                    continue
                buy, sell = extrema

                rec = _compute_opportunity(
                    scan_no, pair_key, buy, sell,
                    token_prices, sentinel, tip_optimizer, trade_size_usd,
                    flash_loan_fee_rate=flash_loan_fee_rate,
                    min_net_profit_usd=min_net_profit_usd,
                    min_flash_loan_usd=min_flash_loan_usd,
                    max_flash_loan_usd=max_flash_loan_usd,
                    max_flash_tvl_fraction=max_flash_tvl_fraction,
                    flash_size_scan_fractions=flash_size_scan_fractions,
                )
                if rec:
                    records.append(rec)
                    logger.info(
                        "  #%03d  %-14s  buy_usdc=$%.8f  sell_usdc=$%.8f"
                        "  spread=%.1fbps  net_edge=$%+.2f"
                        "  p_fill=%.2f  E[profit]=$%+.2f  %s",
                        len(records),
                        rec.pair,
                        rec.buy_price_usdc,
                        rec.sell_price_usdc,
                        rec.raw_spread_bps,
                        rec.expected_net_edge,
                        rec.p_fill,
                        rec.e_profit,
                        "âœ“" if rec.profitable else "âœ—",
                    )
                if not unbounded_target and len(records) >= target_count:
                    break

        # Triangular cycle search across all surviving pools
        if enable_triangular and (unbounded_target or len(records) < target_count):
            tri_recs = _scan_triangular_cycles(
                scan_no, graph_pool_map, token_prices, tip_optimizer,
                max_trade_size_usd=trade_size_usd,
                flash_loan_fee_rate=flash_loan_fee_rate,
                min_net_profit_usd=min_net_profit_usd,
            )
            for rec in tri_recs:
                records.append(rec)
                logger.info(
                    "  #%03d  %-22s  size=$%.0f  net=$%+.2f  p_fill=%.2f  E[profit]=$%+.2f  âœ“ TRI",
                    len(records), rec.pair, rec.trade_size_usd,
                    rec.expected_net_edge, rec.p_fill, rec.e_profit,
                )
                if not unbounded_target and len(records) >= target_count:
                    break

        # Expanded N-hop graph scan (4-hop and beyond, fork-safe by default).
        # expanded_graph_scan() is CPU-bound (cycle enumeration + simulation
        # across a size grid), so it is dispatched to the thread-pool executor
        # to avoid blocking the event loop during SSE updates or sleep timers.
        expanded_graph_scan_enabled = _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True)
        live_quote_enabled = _env_bool("LIVE_QUOTE_ENABLED", True)

        # The deterministic expanded graph scan is useful for route discovery,
        # but it is not proof of executability for mixed V2/V3/Balancer paths.
        # When live quotes are enabled, only LIVEQ records may be promoted into
        # the opportunity ledger; EXP remains a CPU-side hint stage.
        if enable_expanded_scan and expanded_graph_scan_enabled and not live_quote_enabled and (
            unbounded_target or len(records) < target_count
        ):
            eg_result: ExpandedGraphScanResult = await loop.run_in_executor(
                None,
                functools.partial(
                    expanded_graph_scan,
                    pool_map=graph_pool_map,
                    token_prices=token_prices,
                    tip_optimizer=tip_optimizer,
                    min_hops=4,
                    max_hops=expanded_max_hops,
                    max_trade_size_usd=trade_size_usd,
                    flash_loan_fee_rate=flash_loan_fee_rate,
                    min_net_profit_usd=min_net_profit_usd,
                    # fork_safe=True (default) — no live execution in CI
                ),
            )
            for candidate in eg_result.candidates:
                cr = candidate.scored_route.cycle
                leg_price = _leg_price_invariant(
                    leg_amounts_in=cr.leg_amounts_in,
                    leg_amounts_out=cr.leg_amounts_out,
                    quote_block=int(w3.eth.block_number) if w3 is not None else 0,
                )
                if _leg_price_has_structural_failure(leg_price):
                    continue
                rec = OpportunityRecord(
                    scan_no=scan_no,
                    timestamp=time.time(),
                    pair=candidate.scored_route.route_label,
                    buy_dex=cr.dexes[0] if cr.dexes else "unknown",
                    sell_dex=cr.dexes[-1] if cr.dexes else "unknown",
                    buy_pool=cr.pools[0] if cr.pools else "",
                    sell_pool=cr.pools[-1] if cr.pools else "",
                    buy_price_usdc=0.0,
                    sell_price_usdc=0.0,
                    spot_spread_bps=round(
                        10_000.0 * cr.gross_profit_usd / max(cr.trade_size_usd, 1.0), 4
                    ),
                    executable_spread_bps=round(
                        10_000.0 * cr.gross_profit_usd / max(cr.trade_size_usd, 1.0), 4
                    ),
                    raw_spread_bps=round(
                        # max(…, 1.0) guards against zero-division when trade_size_usd
                        # is negligibly small; returns 0 in that degenerate case since
                        # gross_profit_usd will also be ~0 for sub-$1 trades.
                        10_000.0 * cr.gross_profit_usd / max(cr.trade_size_usd, 1.0), 4
                    ),
                    flash_size_usd=round(cr.trade_size_usd, 2),
                    trade_size_usd=round(cr.trade_size_usd, 2),
                    gross_profit_usd=round(cr.gross_profit_usd, 4),
                    slippage_cost_usd=0.0,
                    flash_fee_usd=round(cr.flash_fee_usd, 4),
                    gas_cost_usd=round(cr.gas_cost_usd, 4),
                    expected_net_edge=round(cr.net_profit_usd, 4),
                    p_fill=round(cr.p_fill, 4),
                    e_profit=round(cr.e_profit, 4),
                    profitable=cr.profitable,
                    hop_count=cr.hop_count,
                    route_tokens="->".join(cr.tokens),
                    route_pools="->".join(cr.pools),
                    route_dexes="->".join(cr.dexes),
                    route_id=candidate.scored_route.route_id,
                    route_leg_amounts_in=json.dumps(cr.leg_amounts_in),
                    route_leg_amounts_out=json.dumps(cr.leg_amounts_out),
                    route_swap_0_to_1=json.dumps(cr.swap_0_to_1),
                    route_pool_ids=json.dumps(getattr(cr, "pool_ids", [None] * cr.hop_count)),
                    route_curve_coin_indices=json.dumps(getattr(cr, "curve_coin_indices", [None] * cr.hop_count)),
                    **leg_price,
                )
                records.append(rec)
                logger.info(
                    "  #%03d  %-30s  hops=%d  size=$%.0f  net=$%+.2f"
                    "  p_fill=%.2f  E[profit]=$%+.2f  ✓ EXP",
                    len(records), rec.pair, rec.hop_count,
                    rec.trade_size_usd, rec.expected_net_edge,
                    rec.p_fill, rec.e_profit,
                )
                if not unbounded_target and len(records) >= target_count:
                    break

        if enable_expanded_scan and live_quote_enabled and (
            unbounded_target or len(records) < target_count
        ):
            bounded_proof_call = max_scans is not None
            live_quote_cycle_limit = 0
            live_quote_time_budget_seconds = 0.0
            if bounded_proof_call:
                bounded_target = max(1, target_count if target_count > 0 else 1)
                live_quote_cycle_limit = max(10, min(60, bounded_target * 10))
                live_quote_time_budget_seconds = max(30.0, min(90.0, bounded_target * 30.0))
            live_quote_size_grid = _env_float_list(
                "LIVE_QUOTE_FALLBACK_SIZE_GRID_USD",
                [],
            )
            live_quote_recs = await loop.run_in_executor(
                None,
                functools.partial(
                    _scan_live_quoted_cycles,
                    scan_no,
                    w3,
                    graph_pool_map,
                    token_prices,
                    tip_optimizer,
                    max_trade_size_usd=trade_size_usd,
                    flash_loan_fee_rate=flash_loan_fee_rate,
                    min_net_profit_usd=min_net_profit_usd,
                    min_flash_loan_usd=min_flash_loan_usd,
                    max_hops=expanded_max_hops,
                    cycle_limit=live_quote_cycle_limit,
                    time_budget_seconds=live_quote_time_budget_seconds,
                    size_grid_usd=live_quote_size_grid,
                    rpc_rotation=rpc_rotation,
                ),
            )
            for rec in live_quote_recs:
                records.append(rec)
                logger.info(
                    "  #%03d  %-30s  hops=%d  size=$%.0f  net=$%+.2f"
                    "  p_fill=%.2f  E[profit]=$%+.2f  ✓ LIVEQ",
                    len(records),
                    rec.pair,
                    rec.hop_count,
                    rec.trade_size_usd,
                    rec.expected_net_edge,
                    rec.p_fill,
                    rec.e_profit,
                )
                if not unbounded_target and len(records) >= target_count:
                    break

        if unbounded_target or len(records) < target_count:
            await asyncio.sleep(scan_interval_sec)

    # Write CSV
    _assert_no_synthetic_route_records(records)
    csv_path = output_csv or str(
        Path(__file__).parent.parent / "dry_run_results.csv"
    )
    fieldnames = list(asdict(records[0]).keys()) if records else [f.name for f in fields(OpportunityRecord)]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow(asdict(rec))
    logger.info("Results written to: %s", csv_path)

    # Summary statistics
    profitable = [r for r in records if r.profitable]
    if records:
        edges = [r.expected_net_edge for r in records]
        e_profits = [r.e_profit for r in records]
        p_fills = [r.p_fill for r in records]
        spreads = [r.raw_spread_bps for r in records]
        data_tag = "LIVE POLYGON DATA"

        print("\n" + "=" * 72)
        print(f"DRY RUN RESULTS â€” {len(records)} OPPORTUNITY SAMPLE  [{data_tag}]")
        print("=" * 72)
        print(f"  Records captured :  {len(records)}")
        print(f"  Profitable (net>0): {len(profitable)}  ({100*len(profitable)//len(records)}%)")
        print(f"\n  Raw spread (bps)  â€” mean: {sum(spreads)/len(spreads):.2f},"
              f"  min: {min(spreads):.2f},  max: {max(spreads):.2f}")
        print(f"  Expected net edge â€” mean: ${sum(edges)/len(edges):.2f},"
              f"  min: ${min(edges):.2f},  max: ${max(edges):.2f}")
        print(f"  P(fill)           â€” mean: {sum(p_fills)/len(p_fills):.3f},"
              f"  min: {min(p_fills):.3f},  max: {max(p_fills):.3f}")
        print(f"  E[profit]         â€” mean: ${sum(e_profits)/len(e_profits):.2f},"
              f"  min: ${min(e_profits):.2f},  max: ${max(e_profits):.2f}")

        daily_opps_per_pair = 86_400 / max(scan_interval_sec, 1)
        est_daily = sum(e_profits) / len(e_profits) * daily_opps_per_pair * len(_PAIRS)
        print(f"\n  Rough daily E[profit] estimate: ${est_daily:,.0f}/day")
        print(f"  (assumes {daily_opps_per_pair:,.0f} scan cycles/day Ã— {len(_PAIRS)} pairs)")

        # ---- Structural diagnosis ----------------------------------------
        flash_fee_bps = 5.0        # Aave V3 on Polygon
        avg_spread = sum(spreads) / len(spreads)
        median_spread = sorted(spreads)[len(spreads) // 2]
        unprofitable_bps = flash_fee_bps + 60  # rough floor: flash + dual 0.3% pools
        print(f"\n  STRUCTURAL DIAGNOSIS")
        print(f"  Flash-loan fee floor : {flash_fee_bps:.0f} bps (Aave V3)")
        print(f"  Typical dual-pool fee: ~60 bps (2 Ã— 0.3% QSV2 legs)")
        print(f"  Break-even spread    : >{unprofitable_bps:.0f} bps per arb")
        print(f"  Median observed spread: {median_spread:.1f} bps")
        if median_spread < unprofitable_bps:
            print(f"  â†’ Median spread ({median_spread:.1f} bps) is BELOW break-even "
                  f"({unprofitable_bps:.0f} bps).")
            print(f"  â†’ System is structurally unprofitable at ${trade_size_usd:,.0f} "
                  f"trade size with QSV2 counterparts.")
            print(f"  â†’ To reach $500/day: need spread >{unprofitable_bps:.0f} bps on "
                  f"{'~1 opp/min' if est_daily > 0 else 'every scan'}.")
            print(f"  -> Consider: (a) use 0.01% UniV3 pairs only where live quoters prove execution,")
            print(f"               (b) improve private simulation/relay RPC capacity,")
            print(f"               (c) monitor for dislocations large enough to clear flash fees, gas, and owner profit.")
        print("=" * 72 + "\n")

    return records

async def main():
    logger.info("Starting Apex-Omega-v6 live Polygon opportunity scan")
    await run_live_opportunity_scan(
        target_count=int(_env_float("AUTONOMOUS_SCAN_TARGET_COUNT", 0.0)),
        trade_size_usd=_env_float("AUTONOMOUS_MAX_FLASHLOAN_CAP_USD", _env_float("MAX_FLASH_LOAN_USD", 100_000.0)),
        max_scans=None,
        enable_triangular=_env_bool("AUTONOMOUS_ENABLE_TRIANGULAR", False),
        enable_expanded_scan=_env_bool("AUTONOMOUS_ENABLE_EXPANDED_SCAN", True),
    )


if __name__ == "__main__":
    asyncio.run(main())
