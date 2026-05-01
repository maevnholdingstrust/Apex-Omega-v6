#!/usr/bin/env python3
"""
Dry run script for Apex-Omega-v6 Polygon arbitrage system.
Exercises core components and measures performance.

Live scan mode queries real Polygon on-chain data and records
expected_net_edge, p_fill, and E[profit] for 100 opportunities.
"""

import asyncio
import csv
import itertools
import math
import os
import random as _random
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from web3 import Web3

from apex_omega_core.core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.deterministic_slippage import calculate_deterministic_slippage_bps
from apex_omega_core.core.inference import derive_net_edge
from apex_omega_core.core.feature_factory import extract_features
from apex_omega_core.strategies.execution_router import ExecutionRouter
from apex_omega_core.operations.validate_spread_alignment import validate_spread_alignment
from apex_omega_core.core.types import Spread, ArbitrageOpportunity, Pool, FlashLoanConfig
from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor, ArbitrageDetector
from apex_omega_core.core.mev_gas_oracle import (
    GasOracle, GasPriceSnapshot as _GasPriceSnapshot, PFillEstimator, TipOptimizer,
)

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
# Anything that doesn't have ≥2 surviving pools after the liquidity
# filter is dropped automatically — extra entries cost nothing.
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
}

# Auto-generate ALL unordered pair combinations from the token
# registry.  No hand-curated whitelist — let the filter layer decide
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
# time.  Add real pool IDs here as they're verified — the discovery
# pipeline tolerates an empty list.
_BALANCER_W50_POOLS: List[Tuple[str, float]] = []

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
            kind="cpmm",
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
        # Marginal spot price from a tiny probe swap — for StableSwap
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
    trade_size_usd: float
    gross_profit_usd: float
    slippage_cost_usd: float
    flash_fee_usd: float
    gas_cost_usd: float
    expected_net_edge: float   # USD net after slippage + gas
    p_fill: float              # P(inclusion in next block) at optimal tip
    e_profit: float            # E[profit] = p_fill × expected_net_edge (0 when edge ≤ 0)
    profitable: bool


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


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
    """Build executable flash-loan candidate sizes from env fractions."""
    if weaker_pool_tvl_usd <= 0:
        return []
    upper = min(max_flash_loan_usd, max_trade_size_usd, weaker_pool_tvl_usd * max_flash_tvl_fraction)
    if upper < min_flash_loan_usd:
        return []

    sizes = {
        weaker_pool_tvl_usd * frac
        for frac in scan_fractions
        if frac > 0 and min_flash_loan_usd <= weaker_pool_tvl_usd * frac <= upper
    }
    sizes.add(min_flash_loan_usd)
    sizes.add(upper)
    return sorted(size for size in sizes if min_flash_loan_usd <= size <= upper)

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
    return os.getenv("POLYGON_RPC", "https://polygon-rpc.com/")


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
            # Token order is swapped – flip reserves and symbols
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

    # UniV3 – try all fee tiers
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
    out.extend(_fetch_curve_3pool_views(
        w3, _CURVE_AM3CRV["address"], _CURVE_AM3CRV["coins"],
    ))
    return out


def _discover_pools(w3: Web3, max_workers: int = 12) -> Dict[str, List[_PoolSnapshot]]:
    """
    Query UniV3 and QuickSwap V2 for all configured token pairs *in
    parallel*.  web3.py is sync-IO-bound, so a ThreadPoolExecutor is
    sufficient to overlap RPC roundtrips without GIL contention.

    With 12 workers and ~13 pairs this drops a typical Polygon scan
    from ~54s sequential → ~5-8s.
    """
    snapshots: Dict[str, List[_PoolSnapshot]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_discover_pair, w3, a, b) for (a, b) in _PAIRS]
        ext_future = pool.submit(_discover_external_pools, w3)
        for fut in as_completed(futures):
            try:
                pair_key, pools = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("pair discovery failed: %s", exc)
                continue
            if pools:
                snapshots[pair_key] = pools
        # Merge Balancer + Curve snapshots into the same pair buckets
        try:
            external = ext_future.result()
        except Exception as exc:  # noqa: BLE001
            logger.debug("external pool discovery failed: %s", exc)
            external = []
        for snap in external:
            key = f"{snap.sym0}/{snap.sym1}"
            snapshots.setdefault(key, []).append(snap)
    return snapshots


def _filter_pool_universe(
    pool_map: Dict[str, List["_PoolSnapshot"]],
    token_prices: Dict[str, float],
    min_tvl_usd: float = 0.0,
    max_price_dev: float = 0.05,
) -> Dict[str, List["_PoolSnapshot"]]:
    """Drop stale / mis-priced pools before scoring.

    Filter:
      **Price sanity gate** — drop any pool whose price deviates
      from the *median* price across all pools for that pair by more
      than ``max_price_dev`` (default 5%).  Catches stale single-tick
      UniV3 pools and oracle-divergent venues.  No TVL floor is applied
      here; flash-loan sizing is capped by the smallest pool TVL in the
      swap route instead.
    """
    cleaned: Dict[str, List["_PoolSnapshot"]] = {}
    for pair_key, pools in pool_map.items():
        if len(pools) < 2:
            continue  # need at least two pools to arb

        # Price-sanity filter (median anchor)
        prices = sorted(s.price for s in pools)
        median = prices[len(prices) // 2]
        if median <= 0:
            continue
        survivors = [
            s for s in pools
            if abs(s.price - median) / median <= max_price_dev
        ]
        if len(survivors) >= 2:
            cleaned[pair_key] = survivors

    return cleaned


def _derive_token_prices_usd(
    pool_map: Dict[str, List[_PoolSnapshot]]
) -> Dict[str, float]:
    """
    Estimate USD prices for each token.
    Stablecoins are pegged at $1.00.
    Other tokens are priced from their best (largest reserve) USDC pool.
    """
    stables = {"USDC", "USDT", "DAI"}
    prices: Dict[str, float] = {s: 1.0 for s in stables}

    for pair_key, pools in pool_map.items():
        sym0, sym1 = pair_key.split("/")
        for snap in pools:
            # Price: sym1 per sym0 (both normalised)
            if snap.price <= 0 or not math.isfinite(snap.price):
                continue
            if sym0 in stables and sym1 not in prices:
                prices[sym1] = 1.0 / snap.price      # sym1 USD = 1 / (sym1_per_sym0)
            if sym1 in stables and sym0 not in prices:
                prices[sym0] = snap.price             # sym0 USD = sym1_per_sym0 (stable)

    # Fallback conservative values for any token still missing
    fallbacks = {"WMATIC": 0.40, "WETH": 2500.0, "WBTC": 65000.0, "LINK": 12.0, "AAVE": 120.0}
    for sym, price in fallbacks.items():
        if sym not in prices:
            prices[sym] = price

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
    min_net_profit_usd: float = 1.0,
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

    # Fast prefilter: spot spread must be positive.
    # buy.price > sell.price: we buy token1 cheaply (more token1 per token0)
    # then sell token1 where it fetches more token0.
    # No AMM math here — just a quick reserve-ratio screen before we do
    # the heavier optimal-size and executable-price calculations below.
    spot_spread_bps = (buy.price - sell.price) / sell.price * 10_000.0
    if spot_spread_bps < min_spread_bps:
        return None
    # raw_spread_bps starts as the spot estimate; upgraded to executable below.
    raw_spread_bps = spot_spread_bps

    sym0, sym1 = pair_key.split("/")
    price0 = token_prices.get(sym0, 1.0)
    price1 = token_prices.get(sym1, 1.0)

    # ------------------------------------------------------------------
    # Flash-loan sizing: scan the configured fractions of weaker-pool TVL
    # and keep only the best net-positive candidate.  This prevents
    # dust-sized mathematical spreads from reaching the execution path.
    # ------------------------------------------------------------------
    buy_tvl_usd = buy.reserve0 * price0 + buy.reserve1 * price1
    sell_tvl_usd = sell.reserve0 * price0 + sell.reserve1 * price1
    size_candidates_usd = _flash_size_candidates_usd(
        weaker_pool_tvl_usd=min(buy_tvl_usd, sell_tvl_usd),
        min_flash_loan_usd=min_flash_loan_usd,
        max_flash_loan_usd=max_flash_loan_usd,
        max_trade_size_usd=trade_size_usd,
        max_flash_tvl_fraction=max_flash_tvl_fraction,
        scan_fractions=flash_size_scan_fractions or [0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15],
    )
    if not size_candidates_usd:
        return None

    cap_amount_in = trade_size_usd / price0
    best_amount_in = 0.0
    best_size_usd = 0.0
    best_expected_net = -math.inf
    for candidate_size_usd in size_candidates_usd:
        candidate_amount_in = min(candidate_size_usd / price0, cap_amount_in)
        if candidate_amount_in <= 0.0:
            continue
        candidate_b_out = sentinel.amm_swap(candidate_amount_in, buy.reserve0, buy.reserve1, buy.fee)
        if candidate_b_out <= 0.0:
            continue
        candidate_a_out = sentinel.amm_swap(candidate_b_out, sell.reserve1, sell.reserve0, sell.fee)
        candidate_size_actual_usd = candidate_amount_in * price0
        candidate_gross = candidate_a_out * price0 - candidate_size_actual_usd
        candidate_flash_fee = candidate_size_actual_usd * flash_loan_fee_rate
        candidate_eip1559 = tip_optimizer.build_eip1559_params(max(candidate_gross - candidate_flash_fee, 0.01))
        candidate_net = candidate_gross - candidate_flash_fee - candidate_eip1559["gas_cost_usd"]
        if candidate_net > best_expected_net:
            best_amount_in = candidate_amount_in
            best_size_usd = candidate_size_actual_usd
            best_expected_net = candidate_net
    if best_size_usd < min_flash_loan_usd or best_expected_net < min_net_profit_usd:
        return None
    amount_in = best_amount_in
    actual_trade_size_usd = best_size_usd

    # ------------------------------------------------------------------
    # Cycle-best executable prices at the selected flash-loan size.
    #
    # These are the real AMM-output prices with fee and price-impact
    # already baked in — not the spot reserve ratios used above.
    # Wiring them into every route leg ensures that C1, C2, the pipeline
    # audit, and the dashboard all read the same cycle-lowest buy price
    # and cycle-highest sell price without needing to re-simulate.
    #
    #   best_buy_price_exec  = token0 paid  per token1 received  (lower  = better buy)
    #   best_sell_price_exec = token0 received per token1 sold   (higher = better sell)
    #
    # Profit formula (per unit of token1):
    #   profit_per_t1 = best_sell_price_exec − best_buy_price_exec − tx_costs
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
    # the pool is too shallow to absorb the trade — without relying on
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

    # 2-leg route: token0 → token1 on buy pool, then token1 → token0 on sell pool.
    # Each leg carries the cycle-best executable prices so every downstream
    # consumer (C1, C2, pipeline audit, dashboard) can read the lowest buy
    # and highest sell price for this cycle directly from the artifact.
    route = [
        {
            "venue": buy.dex,
            "pair": f"{sym0} → {sym1}",
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
            "pair": f"{sym1} → {sym0}",
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

    expected_net_edge = adjusted_gross - gas_cost
    e_profit = expected_net_edge * p_fill if expected_net_edge > 0 else 0.0

    # Owner-profit gate: only emit when there is at least
    # ``min_net_profit_usd`` left for the owner after ALL expenses
    # (slippage + DEX fees + flash-loan fee + gas).
    if expected_net_edge < min_net_profit_usd:
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
        trade_size_usd=round(actual_trade_size_usd, 2),
        gross_profit_usd=round(gross_profit, 4),
        slippage_cost_usd=round(slippage_cost, 4),
        flash_fee_usd=round(flash_fee, 4),
        gas_cost_usd=round(gas_cost, 4),
        expected_net_edge=round(expected_net_edge, 4),
        p_fill=round(p_fill, 4),
        e_profit=round(e_profit, 4),
        profitable=(expected_net_edge > 0),
    )


# ---------------------------------------------------------------------------
# Live scan: calibrated offline simulation
# ---------------------------------------------------------------------------

# Pool templates: (pair_key, dex_a, fee_a, dex_b, fee_b,
#                  tvl_usd_a, tvl_usd_b, base_price,
#                  spread_bps_mean, spread_bps_std)
# Derived from historical Polygon DEX liquidity and spread data.
_SIM_TEMPLATES = [
    # Stablecoin pairs — very tight spreads, high TVL
    # base_price = AMM ratio: token1_normalised / token0_normalised
    #            = price_token0_usd / price_token1_usd
    ("USDC/USDT", "univ3_100",  0.0001, "qsv2",        0.003,  8_000_000,  3_000_000, 1.0,       1.0,  0.5),
    ("USDC/USDT", "univ3_500",  0.0005, "univ3_100",   0.0001, 4_000_000,  8_000_000, 1.0,       0.6,  0.3),
    ("USDC/DAI",  "univ3_100",  0.0001, "qsv2",        0.003,  5_000_000,  1_200_000, 1.0,       1.2,  0.6),
    ("USDT/DAI",  "univ3_100",  0.0001, "univ3_500",   0.0005, 2_000_000,  1_500_000, 1.0,       0.8,  0.4),
    # MATIC/stable pairs — moderate spread, medium TVL
    # WMATIC($0.40)/USDC($1.00): ratio = 0.40/1.0 = 0.40 USDC per WMATIC
    ("WMATIC/USDC", "univ3_500",  0.0005, "qsv2",        0.003,  6_000_000,  4_000_000, 0.40,      8.0,  4.0),
    ("WMATIC/USDC", "univ3_3000", 0.003,  "univ3_500",   0.0005, 1_500_000,  6_000_000, 0.40,     12.0,  5.0),
    ("WMATIC/USDT", "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  2_000_000, 0.40,      9.0,  4.5),
    ("WMATIC/DAI",  "univ3_500",  0.0005, "qsv2",        0.003,  1_500_000,  800_000,   0.40,     11.0,  5.0),
    # ETH/stable pairs — moderate spread, high TVL
    # USDC($1)/WETH($2500): ratio = 1.0/2500 = 0.0004 WETH per USDC
    ("USDC/WETH",   "univ3_500",  0.0005, "qsv2",        0.003,  9_000_000,  5_000_000, 4.0e-4,    6.0,  3.5),
    ("USDC/WETH",   "univ3_3000", 0.003,  "univ3_500",   0.0005, 2_500_000,  9_000_000, 4.0e-4,   14.0,  6.0),
    ("USDT/WETH",   "univ3_500",  0.0005, "qsv2",        0.003,  4_000_000,  3_000_000, 4.0e-4,    7.0,  3.5),
    ("DAI/WETH",    "univ3_500",  0.0005, "qsv2",        0.003,  2_000_000,  1_500_000, 4.0e-4,    8.5,  4.0),
    # WMATIC($0.40)/WETH($2500): ratio = 0.40/2500 = 1.6e-4 WETH per WMATIC
    ("WMATIC/WETH", "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  2_000_000, 1.6e-4,   10.0,  5.0),
    # BTC pairs — wider spreads due to lower liquidity
    # USDC($1)/WBTC($65000): ratio = 1/65000 ≈ 1.538e-5 WBTC per USDC
    ("USDC/WBTC",   "univ3_500",  0.0005, "qsv2",        0.003,  3_000_000,  1_200_000, 1.538e-5, 15.0,  8.0),
    ("USDC/WBTC",   "univ3_3000", 0.003,  "univ3_500",   0.0005, 800_000,    3_000_000, 1.538e-5, 22.0,  9.0),
    # WETH($2500)/WBTC($65000): ratio = 2500/65000 ≈ 0.0385 WBTC per WETH
    ("WETH/WBTC",   "univ3_500",  0.0005, "qsv2",        0.003,  2_000_000,  900_000,   0.0385,   18.0,  8.0),
    # DeFi tokens — widest spreads, thinner liquidity
    # USDC($1)/LINK($12): ratio = 1/12 ≈ 0.0833 LINK per USDC
    ("USDC/LINK",   "univ3_3000", 0.003,  "qsv2",        0.003,  1_000_000,  600_000,   0.0833,   25.0, 12.0),
    # WMATIC($0.40)/LINK($12): ratio = 0.40/12 ≈ 0.0333 LINK per WMATIC
    ("WMATIC/LINK", "univ3_3000", 0.003,  "qsv2",        0.003,  500_000,    400_000,   0.0333,   30.0, 14.0),
    # USDC($1)/AAVE($120): ratio = 1/120 ≈ 0.00833 AAVE per USDC
    ("USDC/AAVE",   "univ3_3000", 0.003,  "qsv2",        0.003,  800_000,    500_000,   0.00833,  28.0, 13.0),
    # WMATIC($0.40)/AAVE($120): ratio = 0.40/120 ≈ 0.00333 AAVE per WMATIC
    ("WMATIC/AAVE", "univ3_3000", 0.003,  "qsv2",        0.003,  400_000,    350_000,   0.00333,  35.0, 15.0),
    # WETH($2500)/LINK($12): ratio = 2500/12 ≈ 208.3 LINK per WETH
    ("WETH/LINK",   "univ3_3000", 0.003,  "qsv2",        0.003,  600_000,    450_000,   208.3,    22.0, 10.0),
    # WETH($2500)/AAVE($120): ratio = 2500/120 ≈ 20.83 AAVE per WETH
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

        # Reserves: token0 in normalised units, token1 = r0 × AMM_ratio
        # (balanced pool: half TVL in each token)
        r0_a = (tvl_a / 2.0) / price0_usd
        r1_a = r0_a * price_a          # ← correct: r1 = r0 × (token1/token0)

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
    if pool.kind == "curve_ss":
        # 2-coin pairwise view of the n-coin pool: i=0 if swapping
        # token0→token1 (matches sym0→sym1 ordering), else i=1.
        i, j = (0, 1) if swap_0_to_1 else (1, 0)
        balances = [pool.reserve0, pool.reserve1]
        return _curve_get_dy(i, j, amount_in, balances, pool.amp, pool.fee)
    # CPMM default
    r_in, r_out = (pool.reserve0, pool.reserve1) if swap_0_to_1 else (pool.reserve1, pool.reserve0)
    return _cpmm_swap_out(amount_in, r_in, r_out, pool.fee)


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

    * **best_buy_pool** — pool with the *highest* token1-per-token0 spot price
      (``reserve1 / reserve0``).  Buying token1 here is cheapest: you receive
      the most token1 per unit of token0 spent.

    * **best_sell_pool** — pool with the *lowest* token1-per-token0 spot price.
      Selling token1 here is most profitable: the pool values token1 most
      highly relative to token0, so you receive the most token0 per token1
      sold.

    The cross-pool spread ``best_buy_pool.price − best_sell_pool.price`` is
    always ≥ the spread of any other ``(i, j)`` combination from the same
    list, so testing only this pair yields the maximum possible raw edge
    without iterating O(N²) combinations.

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

    # Most token1 per token0 → cheapest place to buy token1
    best_buy = max(eligible, key=lambda p: p.price)
    # Least token1 per token0 → best place to sell token1 (most token0 back)
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
    """Simulate A→B→C→A and return token-A delta (negative = loss)."""
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
    """Search every A→B→C→A cycle for owner-positive net profit.

    Uses a 24-point geometric grid from $50 to ``max_trade_size_usd``
    over the input principal in token A.  Picks the size that maximises
    USD profit after slippage, DEX fees, flash fee, and gas.  Emits a
    record only when net profit ≥ ``min_net_profit_usd``.
    """
    out: List[OpportunityRecord] = []
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

        # Try both rotation directions: A→B→C→A and A→C→B→A
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
            best_size_usd = 0.0
            best_gross_usd = 0.0
            for size_usd in grid:
                x_in_a = size_usd / price_t0_usd
                delta_a = _triangular_profit_in_token_a(x_in_a, leg01, leg12, leg20)
                gross_usd = delta_a * price_t0_usd
                flash_fee = size_usd * flash_loan_fee_rate
                eip1559 = tip_optimizer.build_eip1559_params(max(gross_usd - flash_fee, 0.01))
                # Triangular costs ~3 swaps vs 2; charge ~1.5x gas
                gas_cost = eip1559["gas_cost_usd"] * 1.5
                net = gross_usd - flash_fee - gas_cost
                if net > best_net:
                    best_net = net
                    best_size_usd = size_usd
                    best_gross_usd = gross_usd

            if best_net < min_net_profit_usd:
                continue

            eip1559 = tip_optimizer.build_eip1559_params(max(best_net, 0.01))
            gas_cost = eip1559["gas_cost_usd"] * 1.5
            p_fill = eip1559["p_fill"]
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
                trade_size_usd=round(best_size_usd, 2),
                gross_profit_usd=round(best_gross_usd, 4),
                slippage_cost_usd=0.0,  # already netted into gross via CPMM math
                flash_fee_usd=round(flash_fee, 4),
                gas_cost_usd=round(gas_cost, 4),
                expected_net_edge=round(best_net, 4),
                p_fill=round(p_fill, 4),
                e_profit=round(best_net * p_fill, 4),
                profitable=True,
            ))
    return out


_FLASH_LOAN_PROVIDERS: Dict[str, float] = {
    "balancer": 0.0,        # Balancer V2 vault flash loans — no fee
    "aave_v3": 0.0009,      # Aave V3 — 9 bps
    "uniswap_v3": 0.0,      # UniV3 flash via callback — only the pool fee
    "none": 0.0,            # Own-capital execution (no flash loan)
}


def _resolve_flash_loan_fee_rate(provider: Optional[str]) -> float:
    fee_bps = os.getenv("FLASH_LOAN_FEE_BPS")
    if fee_bps not in (None, ""):
        return float(fee_bps) / 10_000.0
    name = (provider or os.getenv("FLASH_LOAN_PROVIDER", "balancer")).lower()
    return _FLASH_LOAN_PROVIDERS.get(name, _FLASH_LOAN_PROVIDERS["balancer"])


async def run_live_opportunity_scan(
    rpc_url: Optional[str] = None,
    target_count: int = 100,
    scan_interval_sec: float = 2.0,
    output_csv: Optional[str] = None,
    trade_size_usd: float = 10_000.0,
    flash_loan_provider: Optional[str] = None,
    min_pool_tvl_usd: float = 0.0,
    max_price_dev: float = 0.05,
    min_net_profit_usd: float = 1.0,
    enable_triangular: bool = True,
    max_scans: Optional[int] = None,
) -> List[OpportunityRecord]:
    """
    Scan real Polygon DEX pools and record ``target_count`` opportunity
    observations.  For each cross-DEX price discrepancy the following
    metrics are logged:

    * **expected_net_edge** – net USD profit after slippage, DEX fees,
      flash-loan fee, and gas cost.
    * **p_fill** – logistic P(inclusion in the next block) at the
      EIP-1559 tip that maximises E[profit].
    * **E[profit]** – ``p_fill × expected_net_edge`` (0 when edge ≤ 0).

    Raises
    ------
    ConnectionError
        When the Polygon RPC endpoint cannot be reached after 3 attempts.
        Simulation fallback is intentionally not provided — live data is
        mandatory.  Set ``POLYGON_RPC`` (or ``POLYGON_HTTP`` /
        ``ALCHEMY_HTTP_1``) to a reachable Polygon mainnet endpoint.
    """
    rpc = rpc_url or _load_rpc_url()
    logger.info("Connecting to Polygon RPC: %s", rpc[:60] + "…")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))

    # Retry the connection check up to 3 times before failing hard.
    # Simulation fallback is intentionally removed — live data is mandatory.
    _connected = False
    for _attempt in range(1, 4):
        if w3.is_connected():
            _connected = True
            break
        logger.warning(
            "RPC connection attempt %d/3 failed (%s). Retrying in 2 s…",
            _attempt, rpc[:60],
        )
        await asyncio.sleep(2)
    if not _connected:
        raise ConnectionError(
            f"Cannot reach Polygon RPC after 3 attempts. "
            "Set POLYGON_RPC (or POLYGON_HTTP / ALCHEMY_HTTP_1) to a reachable "
            "Polygon mainnet endpoint and restart."
        )
    logger.info("Connected. Block #%d", w3.eth.block_number)

    sentinel = SlippageSentinel()
    gas_oracle = GasOracle(rpc_url=rpc, w3=w3)
    flash_loan_fee_rate = _resolve_flash_loan_fee_rate(flash_loan_provider)
    min_flash_loan_usd = _env_float("MIN_FLASH_LOAN_USD", 50.0)
    max_flash_loan_usd = _env_float("MAX_FLASH_LOAN_USD", 1_000_000.0)
    max_flash_tvl_fraction = _env_float("MAX_FLASH_TVL_FRACTION", 0.15)
    flash_size_scan_fractions = _env_float_list(
        "FLASH_SIZE_SCAN_FRACTIONS",
        [0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15],
    )
    logger.info(
        "Flash-loan provider: %s (fee=%.2f bps, min=$%.0f, max=$%.0f, tvl_cap=%.2f)",
        (flash_loan_provider or os.getenv("FLASH_LOAN_PROVIDER", "balancer")),
        flash_loan_fee_rate * 10_000.0,
        min_flash_loan_usd,
        max_flash_loan_usd,
        max_flash_tvl_fraction,
    )

    records: List[OpportunityRecord] = []
    scan_no = 0

    logger.info(
        "Starting live scan: target=%d opportunities, trade_size=$%.0f",
        target_count,
        trade_size_usd,
    )

    loop = asyncio.get_running_loop()

    while len(records) < target_count:
        if max_scans is not None and scan_no >= max_scans:
            logger.info(
                "Reached max_scans=%d with %d/%d records — stopping.",
                max_scans, len(records), target_count,
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
        token_prices = _derive_token_prices_usd(pool_map)
        # Apply liquidity + price-sanity filters before scoring so we
        # never rank stale single-tick UniV3 pools or dust venues.
        pool_map = _filter_pool_universe(
            pool_map, token_prices,
            min_tvl_usd=min_pool_tvl_usd,
            max_price_dev=max_price_dev,
        )

        mode_tag = "LIVE"
        logger.info(
            "[%s] Scan #%d: %d pairs found (%.1fs). Records so far: %d/%d",
            mode_tag,
            scan_no,
            len(pool_map),
            time.time() - scan_start,
            len(records),
            target_count,
        )

        for pair_key, pools in sorted(pool_map.items()):
            # Cycle-extrema selection: identify the globally-best buy pool
            # (cheapest acquisition — highest token1-per-token0) and the
            # globally-best sell pool (highest exit — lowest token1-per-token0)
            # for this pair in this scan cycle.
            #
            # Testing only the optimal (best_buy, best_sell) pair — not all
            # O(N²) combinations — is correct because the cross-pool spread
            # max(prices) − min(prices) is always ≥ any other combination, and
            # the executable prices are now embedded in the route artifact so
            # every downstream stage reads the cycle-lowest buy and cycle-highest
            # sell without re-scanning.
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
                    "✓" if rec.profitable else "✗",
                )
            if len(records) >= target_count:
                break

        # Triangular cycle search across all surviving pools
        if enable_triangular and len(records) < target_count:
            tri_recs = _scan_triangular_cycles(
                scan_no, pool_map, token_prices, tip_optimizer,
                max_trade_size_usd=trade_size_usd,
                flash_loan_fee_rate=flash_loan_fee_rate,
                min_net_profit_usd=min_net_profit_usd,
            )
            for rec in tri_recs:
                records.append(rec)
                logger.info(
                    "  #%03d  %-22s  size=$%.0f  net=$%+.2f  p_fill=%.2f  E[profit]=$%+.2f  ✓ TRI",
                    len(records), rec.pair, rec.trade_size_usd,
                    rec.expected_net_edge, rec.p_fill, rec.e_profit,
                )
                if len(records) >= target_count:
                    break

        if len(records) < target_count:
            await asyncio.sleep(scan_interval_sec)

    # Write CSV
    csv_path = output_csv or str(
        Path(__file__).parent.parent / "dry_run_results.csv"
    )
    fieldnames = list(asdict(records[0]).keys()) if records else []
    with open(csv_path, "w", newline="") as fh:
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
        print(f"DRY RUN RESULTS — {len(records)} OPPORTUNITY SAMPLE  [{data_tag}]")
        print("=" * 72)
        print(f"  Records captured :  {len(records)}")
        print(f"  Profitable (net>0): {len(profitable)}  ({100*len(profitable)//len(records)}%)")
        print(f"\n  Raw spread (bps)  — mean: {sum(spreads)/len(spreads):.2f},"
              f"  min: {min(spreads):.2f},  max: {max(spreads):.2f}")
        print(f"  Expected net edge — mean: ${sum(edges)/len(edges):.2f},"
              f"  min: ${min(edges):.2f},  max: ${max(edges):.2f}")
        print(f"  P(fill)           — mean: {sum(p_fills)/len(p_fills):.3f},"
              f"  min: {min(p_fills):.3f},  max: {max(p_fills):.3f}")
        print(f"  E[profit]         — mean: ${sum(e_profits)/len(e_profits):.2f},"
              f"  min: ${min(e_profits):.2f},  max: ${max(e_profits):.2f}")

        daily_opps_per_pair = 86_400 / max(scan_interval_sec, 1)
        est_daily = sum(e_profits) / len(e_profits) * daily_opps_per_pair * len(_PAIRS)
        print(f"\n  Rough daily E[profit] estimate: ${est_daily:,.0f}/day")
        print(f"  (assumes {daily_opps_per_pair:,.0f} scan cycles/day × {len(_PAIRS)} pairs)")

        # ---- Structural diagnosis ----------------------------------------
        flash_fee_bps = 9.0        # Aave V3 on Polygon
        avg_spread = sum(spreads) / len(spreads)
        median_spread = sorted(spreads)[len(spreads) // 2]
        unprofitable_bps = flash_fee_bps + 60  # rough floor: flash + dual 0.3% pools
        print(f"\n  STRUCTURAL DIAGNOSIS")
        print(f"  Flash-loan fee floor : {flash_fee_bps:.0f} bps (Aave V3)")
        print(f"  Typical dual-pool fee: ~60 bps (2 × 0.3% QSV2 legs)")
        print(f"  Break-even spread    : >{unprofitable_bps:.0f} bps per arb")
        print(f"  Median observed spread: {median_spread:.1f} bps")
        if median_spread < unprofitable_bps:
            print(f"  → Median spread ({median_spread:.1f} bps) is BELOW break-even "
                  f"({unprofitable_bps:.0f} bps).")
            print(f"  → System is structurally unprofitable at ${trade_size_usd:,.0f} "
                  f"trade size with QSV2 counterparts.")
            print(f"  → To reach $500/day: need spread >{unprofitable_bps:.0f} bps on "
                  f"{'~1 opp/min' if est_daily > 0 else 'every scan'}.")
            print(f"  → Consider: (a) use 0.01% UniV3 pairs only (floor drops to ~11 bps),")
            print(f"               (b) own-capital (no flash loan, floor drops to ~2 bps),")
            print(f"               (c) monitor for flash-crash events (spreads >200 bps).")
        print("=" * 72 + "\n")

    return records

async def main():
    logger.info("Starting Apex-Omega-v6 Polygon Arbitrage Dry Run")

    # Sample data
    spread = Spread(symbol='USDC', bid=1.0000, ask=1.0005, timestamp=time.time())
    data = {'edge': 0.05, 'price': 100.0, 'volume': 1000}

    # Test spread alignment
    logger.info("Testing Spread Alignment")
    start = time.time()
    aligned = align_spread(spread)
    end = time.time()
    print(f"Original: bid={spread.bid}, ask={spread.ask}")
    print(f"Aligned: bid={aligned.bid}, ask={aligned.ask}")
    logger.info(f"Spread alignment completed in {end - start:.6f}s")

    # BPS conversions
    logger.info("Testing BPS Conversions")
    start = time.time()
    bps = decimal_to_bps(0.01)
    decimal = bps_to_decimal(bps)
    end = time.time()
    print(f"0.01 to BPS: {bps}, back to decimal: {decimal}")
    logger.info(f"BPS conversions completed in {end - start:.6f}s")

    # Slippage sentinel with DEX routing
    logger.info("Testing DEX-Aware Slippage Sentinel")
    sentinel = SlippageSentinel()
    start = time.time()
    protocol = sentinel.route(data, ['uniswap', 'sushiswap'])
    # Mock arbitrage opportunity
    buy_pool = Pool("0x123", "uniswap", "USDC", "WETH", 1000000, 0.003)
    sell_pool = Pool("0x456", "sushiswap", "USDC", "WETH", 800000, 0.003)
    mock_opp = ArbitrageOpportunity(
        token="USDC",
        buy_pool=buy_pool,
        sell_pool=sell_pool,
        buy_price=1.0,
        sell_price=1.005,
        spread_bps=50,
        estimated_profit_usd=250,
        flash_loan_amount=50000,
        flash_loan_token="USDC",
        path=["0x123", "0x456"],
        gas_estimate=0.1
    )
    flash_size = sentinel.calculate_flash_loan_size(mock_opp)
    end = time.time()
    print(f"Routed DEX: {protocol}")
    print(f"Flash loan size for opportunity: ${flash_size:,.0f}")
    logger.info(f"Slippage sentinel completed in {end - start:.6f}s")

    # Inference
    logger.info("Testing Inference")
    start = time.time()
    result = derive_net_edge(data)
    end = time.time()
    print(f"Net edge: {result.net_edge}")
    print(f"Features: {[f'{f.name}: {f.value}' for f in result.features]}")
    logger.info(f"Inference completed in {end - start:.6f}s")

    # Feature extraction
    logger.info("Testing Feature Extraction")
    start = time.time()
    features = extract_features(data)
    end = time.time()
    print(f"Extracted features: {[f'{f.name}: {f.value}' for f in features]}")
    logger.info(f"Feature extraction completed in {end - start:.6f}s")

    # Arbitrage detection
    logger.info("Testing Arbitrage Detection")
    dex_monitor = PolygonDEXMonitor()
    detector = ArbitrageDetector(dex_monitor, FlashLoanConfig())
    tokens = ["0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"]  # USDC
    start = time.time()
    opportunities = await detector.find_opportunities(tokens)
    end = time.time()
    print(f"Found {len(opportunities)} arbitrage opportunities")
    if opportunities:
        opp = opportunities[0]
        print(f"Sample opportunity: {opp.token} spread {opp.spread_bps}bps, profit ${opp.estimated_profit_usd:.2f}")
    logger.info(f"Arbitrage detection completed in {end - start:.6f}s")

    # Execution router with arbitrage
    logger.info("Testing Arbitrage Execution Router")
    router = ExecutionRouter()
    start = time.time()
    if opportunities:
        exec_result = await router.execute_arbitrage(opportunities[0])
        print(f"Arbitrage execution success: {exec_result.success}")
        if exec_result.tx_hash:
            print(f"Transaction hash: {exec_result.tx_hash}")
    else:
        print("No opportunities to execute")
    end = time.time()
    logger.info(f"Execution router completed in {end - start:.6f}s")

    # Validation
    logger.info("Testing Validation")
    start = time.time()
    valid = validate_spread_alignment(spread)
    end = time.time()
    print(f"Alignment valid: {valid}")
    logger.info(f"Validation completed in {end - start:.6f}s")

    logger.info("Polygon Arbitrage Dry Run Complete")

    # Live Polygon scan ---------------------------------------------------
    logger.info("Starting live Polygon opportunity scan (100 records) …")
    await run_live_opportunity_scan(target_count=100, trade_size_usd=10_000.0)


if __name__ == "__main__":
    asyncio.run(main())
