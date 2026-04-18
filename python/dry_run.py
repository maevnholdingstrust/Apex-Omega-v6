#!/usr/bin/env python3
"""
Dry run script for Apex-Omega-v6 Polygon arbitrage system.
Exercises core components and measures performance.

Live scan mode queries real Polygon on-chain data and records
expected_net_edge, p_fill, and E[profit] for 100 opportunities.
"""

import asyncio
import csv
import math
import os
import random as _random
import time
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from web3 import Web3

from apex_omega_core.core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
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

# (checksummed address, decimals)
_TOKENS: Dict[str, Tuple[str, int]] = {
    "USDC":   ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "USDT":   ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":    ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH":   ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC":   ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    "LINK":   ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
    "AAVE":   ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
}

# Canonical pairs to scan (symbol A, symbol B).
# Both UniV3 and V2 sort token0/token1 by address, so comparisons are direct.
_PAIRS: List[Tuple[str, str]] = [
    ("WMATIC", "USDC"),
    ("WMATIC", "USDT"),
    ("WMATIC", "DAI"),
    ("WMATIC", "WETH"),
    ("USDC",   "WETH"),
    ("USDT",   "WETH"),
    ("DAI",    "WETH"),
    ("USDC",   "USDT"),
    ("USDC",   "DAI"),
    ("USDT",   "DAI"),
    ("WETH",   "WBTC"),
    ("USDC",   "WBTC"),
    ("USDC",   "LINK"),
    ("WMATIC", "LINK"),
    ("USDC",   "AAVE"),
    ("WMATIC", "AAVE"),
    ("WETH",   "LINK"),
    ("WETH",   "AAVE"),
]

_UNIV3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
_QSV2_FACTORY  = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"

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
    dex: str              # e.g. "univ3_500", "qsv2"
    fee: float            # as decimal, e.g. 0.003
    # token0/token1 symbols (sorted by address, matching factory ordering)
    sym0: str
    sym1: str
    # Decimal-normalised reserves (1 USDC = 1.0, 1 WETH = 1.0)
    reserve0: float
    reserve1: float
    # token1-per-token0 price (both in normalised units)
    price: float


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
    raw_spread_bps: float
    trade_size_usd: float
    gross_profit_usd: float
    slippage_cost_usd: float
    gas_cost_usd: float
    expected_net_edge: float   # USD net after slippage + gas
    p_fill: float              # P(inclusion in next block) at optimal tip
    e_profit: float            # E[profit] = p_fill × expected_net_edge (0 when edge ≤ 0)
    profitable: bool

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


def _discover_pools(w3: Web3) -> Dict[str, List[_PoolSnapshot]]:
    """
    Query UniV3 and QuickSwap V2 for all configured token pairs.
    Returns a dict keyed by canonical pair name (e.g. "USDC/WETH").
    Pair name uses lexicographic-by-address token0/token1 order.
    """
    snapshots: Dict[str, List[_PoolSnapshot]] = {}

    for sym_a, sym_b in _PAIRS:
        addr_a, dec_a = _TOKENS[sym_a]
        addr_b, dec_b = _TOKENS[sym_b]

        # Determine canonical token0/token1 ordering (lower address first)
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

        if pools:
            snapshots[pair_key] = pools

    return snapshots


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


def _compute_opportunity(
    scan_no: int,
    pair_key: str,
    buy: _PoolSnapshot,
    sell: _PoolSnapshot,
    token_prices: Dict[str, float],
    sentinel: SlippageSentinel,
    tip_optimizer: TipOptimizer,
    trade_size_usd: float,
    min_spread_bps: float = 0.5,
) -> Optional[OpportunityRecord]:
    """
    Compute expected_net_edge, p_fill, and E[profit] for a single
    cross-DEX price discrepancy.  Returns None when spread is below the
    minimum threshold or reserves are too thin to simulate.
    """
    # buy.price > sell.price: we buy token1 cheaply (more token1 per token0)
    # then sell token1 where it fetches more token0.
    raw_spread_bps = (buy.price - sell.price) / sell.price * 10_000.0
    if raw_spread_bps < min_spread_bps:
        return None

    sym0, sym1 = pair_key.split("/")
    price0 = token_prices.get(sym0, 1.0)
    price1 = token_prices.get(sym1, 1.0)

    # Trade size in token0 normalised units
    amount_in = trade_size_usd / price0

    # Skip pools whose active depth is clearly insufficient
    if buy.reserve0 < amount_in * 0.01 or sell.reserve1 < (amount_in * buy.price) * 0.01:
        return None

    # 2-leg route: token0 → token1 on buy pool, then token1 → token0 on sell pool
    route = [
        {
            "venue": buy.dex,
            "pair": f"{sym0} → {sym1}",
            "reserve_in": buy.reserve0,
            "reserve_out": buy.reserve1,
            "fee": buy.fee,
            "price_in_usd": price0,
            "price_out_usd": price1,
        },
        {
            "venue": sell.dex,
            "pair": f"{sym1} → {sym0}",
            "reserve_in": sell.reserve1,
            "reserve_out": sell.reserve0,
            "fee": sell.fee,
            "price_in_usd": price1,
            "price_out_usd": price0,
        },
    ]

    final_out, slippage_legs = sentinel.simulate_route(amount_in, route)

    initial_usd = trade_size_usd
    final_usd = final_out * price0

    gross_profit = final_usd - initial_usd
    total_slippage = sum(
        float(leg.get("usd_in", 0)) - float(leg.get("usd_out", 0))
        for leg in slippage_legs
    )
    slippage_cost = max(0.0, total_slippage)

    # Flash-loan fee (Aave V3 = 9 bps on the principal)
    flash_fee = trade_size_usd * 0.0009
    adjusted_gross = gross_profit - flash_fee

    # Gas cost and P(fill) at the optimal EIP-1559 tip
    eip1559 = tip_optimizer.build_eip1559_params(max(adjusted_gross, 0.01))
    gas_cost = eip1559["gas_cost_usd"]
    p_fill = eip1559["p_fill"]

    expected_net_edge = adjusted_gross - gas_cost
    e_profit = expected_net_edge * p_fill if expected_net_edge > 0 else 0.0

    return OpportunityRecord(
        scan_no=scan_no,
        timestamp=time.time(),
        pair=pair_key,
        buy_dex=buy.dex,
        sell_dex=sell.dex,
        buy_pool=buy.pool_address,
        sell_pool=sell.pool_address,
        raw_spread_bps=round(raw_spread_bps, 4),
        trade_size_usd=trade_size_usd,
        gross_profit_usd=round(gross_profit, 4),
        slippage_cost_usd=round(slippage_cost, 4),
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


async def run_live_opportunity_scan(
    rpc_url: Optional[str] = None,
    target_count: int = 100,
    scan_interval_sec: float = 2.0,
    output_csv: Optional[str] = None,
    trade_size_usd: float = 10_000.0,
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

    When the Polygon RPC is unreachable the function falls back to a
    calibrated simulation that uses realistic pool TVLs, fee tiers, and
    spread distributions derived from historical Polygon DEX data.
    All formulas (AMM slippage, EIP-1559 P(fill), gas cost) are
    **identical** to the live-data path; only the reserve inputs differ.
    """
    rpc = rpc_url or _load_rpc_url()
    logger.info("Connecting to Polygon RPC: %s", rpc[:60] + "…")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))

    use_simulation = False
    if not w3.is_connected():
        logger.warning(
            "Cannot reach Polygon RPC.  Falling back to calibrated simulation."
        )
        use_simulation = True
    else:
        logger.info("Connected. Block #%d", w3.eth.block_number)

    sentinel = SlippageSentinel()
    gas_oracle = GasOracle(rpc_url=rpc, w3=w3)

    # Pre-built simulation gas snapshot: realistic Polygon gas (Jun 2025 baseline)
    _SIM_GAS_SNAP = _GasPriceSnapshot(
        base_fee_gwei=30.0,
        tip_p25_gwei=30.0,
        tip_p50_gwei=35.0,
        tip_p75_gwei=50.0,
        tip_p90_gwei=80.0,
        gas_used_ratio_avg=0.55,
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
        scan_no += 1
        scan_start = time.time()

        # Refresh gas snapshot each scan round
        if not use_simulation:
            gas_oracle.invalidate()
            gas_snap = await loop.run_in_executor(None, gas_oracle.get_snapshot)
        else:
            gas_snap = _SIM_GAS_SNAP
        tip_optimizer = TipOptimizer(gas_snap, gas_units=_GAS_UNITS, chain="polygon")

        # Discover pools: real on-chain or calibrated simulation
        if not use_simulation:
            pool_map = await loop.run_in_executor(None, _discover_pools, w3)
        else:
            pool_map = _simulate_pools(scan_no)
        token_prices = _derive_token_prices_usd(pool_map)

        mode_tag = "SIM" if use_simulation else "LIVE"
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
            if len(pools) < 2:
                continue
            # Compare every combination of pools for the same pair
            for i in range(len(pools)):
                for j in range(i + 1, len(pools)):
                    pool_a, pool_b = pools[i], pools[j]
                    # Buy on the HIGHER price pool (more token1 per token0 =
                    # token1 is cheaper), sell on the LOWER price pool.
                    if pool_a.price >= pool_b.price:
                        buy, sell = pool_a, pool_b
                    else:
                        buy, sell = pool_b, pool_a

                    rec = _compute_opportunity(
                        scan_no, pair_key, buy, sell,
                        token_prices, sentinel, tip_optimizer, trade_size_usd,
                    )
                    if rec:
                        records.append(rec)
                        logger.info(
                            "  #%03d  %-14s  spread=%.1fbps  net_edge=$%+.2f"
                            "  p_fill=%.2f  E[profit]=$%+.2f  %s",
                            len(records),
                            rec.pair,
                            rec.raw_spread_bps,
                            rec.expected_net_edge,
                            rec.p_fill,
                            rec.e_profit,
                            "✓" if rec.profitable else "✗",
                        )
                    if len(records) >= target_count:
                        break
                if len(records) >= target_count:
                    break
            if len(records) >= target_count:
                break

        if len(records) < target_count:
            if not use_simulation:
                await asyncio.sleep(scan_interval_sec)
            else:
                await asyncio.sleep(0)  # yield without blocking in simulation

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
        data_tag = "CALIBRATED SIMULATION" if use_simulation else "LIVE POLYGON DATA"

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
        if use_simulation:
            print("\n  NOTE: RPC unavailable — simulation used calibrated pool parameters.")
            print("  Re-run with a live Polygon RPC to record actual on-chain data.")
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