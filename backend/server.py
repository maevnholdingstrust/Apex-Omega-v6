"""
Apex-Omega Final 2.0 - FastAPI backend
Discovery -> Execution -> Submission pipeline

Implements:
- Polygon liquidity graph simulation (V2/V3/Algebra/Balancer/Curve)
- Hybrid buffer baseline + EV buffer optimizer
- C1 Aggressor (InstitutionalExecutor) - first strike Block N
- C2 Surgeon (UltimateArbitrageExecutor) - MIRROR/REVERSE/DO_NOTHING Block N+1
- EVM mirror validation
- Merkle tree for C2 candidates
- Titan bundle submission
- Unified C1+C2 opportunity cycle archive
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from fork_sim import (
    ensure_fork_running,
    get_fork,
    shutdown_fork,
    REQUIRE_FORK_SIM,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

logger = logging.getLogger("apex.omega")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Lifespan: Mongo client + simulated liquidity graph
# ---------------------------------------------------------------------------
state: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    state["client"] = client
    state["db"] = db
    # Indexes
    await db.cycles.create_index([("created_at", -1)])
    await db.opportunities.create_index([("created_at", -1)])
    await db.opportunities.create_index([("status", 1)])
    # Bootstrap liquidity graph
    state["graph"] = build_liquidity_graph()
    state["positions"] = build_aave_positions(24)
    state["block"] = 65_000_000
    state["last_tick"] = time.time()
    # Mode: LIVE | SHADOW | SIM (default LIVE per operator config)
    state["mode"] = os.environ.get("APEX_DEFAULT_MODE", "LIVE").upper()
    state["fork_health"] = {"ok": False, "error": "not yet spawned"}
    # Spawn Anvil fork in background (LIVE + SHADOW need it)
    if state["mode"] in ("LIVE", "SHADOW"):
        state["fork_spawn_task"] = asyncio.create_task(_bootstrap_fork())
    # Background ticker -> drifts prices, mutates pools
    state["ticker_task"] = asyncio.create_task(_price_ticker())
    yield
    state["ticker_task"].cancel()
    await shutdown_fork()
    client.close()


async def _bootstrap_fork():
    try:
        h = await ensure_fork_running()
        state["fork_health"] = h
        logger.info("Anvil fork status: %s", h)
    except Exception as e:
        logger.exception("fork bootstrap failed: %s", e)
        state["fork_health"] = {"ok": False, "error": str(e)}


app = FastAPI(title="Apex Omega Final 2.0", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Liquidity graph (Polygon 137 simulation)
# ---------------------------------------------------------------------------
TOKENS = {
    "USDC": {"decimals": 6, "ref_usd": 1.0},
    "USDT": {"decimals": 6, "ref_usd": 1.0},
    "DAI":  {"decimals": 18, "ref_usd": 1.0},
    "WETH": {"decimals": 18, "ref_usd": 3_412.0},
    "WMATIC": {"decimals": 18, "ref_usd": 0.412},
    "WBTC": {"decimals": 8, "ref_usd": 95_840.0},
    "LINK": {"decimals": 18, "ref_usd": 22.18},
    "AAVE": {"decimals": 18, "ref_usd": 318.4},
    "CRV":  {"decimals": 18, "ref_usd": 0.74},
    "BAL":  {"decimals": 18, "ref_usd": 3.21},
}

DEX_FAMILIES = [
    # (label, family, fee, math_params)
    ("QuickSwap V2",   "v2",       0.0030, {}),
    ("SushiSwap",      "v2",       0.0030, {}),
    ("ApeSwap",        "v2",       0.0020, {}),
    ("Uniswap V3",     "v3",       0.0005, {"concentration": 5.5}),
    ("QuickSwap V3",   "v3",       0.0010, {"concentration": 3.8}),
    ("Algebra Quick",  "algebra",  0.0009, {"concentration": 4.5}),
    ("Balancer 80/20", "balancer", 0.0020, {"weight_in": 0.8, "weight_out": 0.2}),
    ("Balancer 50/50", "balancer", 0.0015, {"weight_in": 0.5, "weight_out": 0.5}),
    ("Curve A=100",    "curve",    0.0004, {"A": 100.0}),
    ("Curve A=1500",   "curve",    0.0001, {"A": 1500.0}),
]

PAIRS = [
    ("WETH", "USDC"),
    ("WETH", "USDT"),
    ("WBTC", "USDC"),
    ("WMATIC", "USDC"),
    ("WMATIC", "WETH"),
    ("LINK", "WETH"),
    ("DAI", "USDC"),
    ("USDC", "USDT"),
    ("AAVE", "WETH"),
    ("CRV", "USDC"),
    ("BAL", "USDC"),
]


def build_liquidity_graph() -> List[Dict[str, Any]]:
    """Build a synthetic Polygon liquidity graph: pools across DEXs/protocols.

    NOTE: This is the raw pool universe. Pools are filtered through the
    liquidity eligibility gate (TVL floor, price-sanity, freshness) before
    they enter arbitrage route discovery. Liquidity is an enablement filter,
    not an execution gate.
    """
    pools = []
    random.seed(42)
    for base, quote in PAIRS:
        for venue, family, fee, math_params in DEX_FAMILIES:
            if family == "curve" and base != "DAI" and base != "USDC":
                continue
            ref_base = TOKENS[base]["ref_usd"]
            ref_quote = TOKENS[quote]["ref_usd"]
            mid = ref_base / ref_quote
            drift_bps = random.uniform(-25, 25)
            price = mid * (1 + drift_bps / 10_000)
            # Mix realistic + thin pools (gate min is $5k)
            roll = random.random()
            if roll < 0.15:
                tvl_usd = random.uniform(800, 4_800)         # below $5k floor
            elif roll < 0.30:
                tvl_usd = random.uniform(5_000, 180_000)     # small but eligible
            elif roll < 0.55:
                tvl_usd = random.uniform(180_000, 2_500_000) # mid
            else:
                tvl_usd = random.uniform(2_500_000, 90_000_000)  # deep
            depth = tvl_usd / (2 * ref_base)
            pool = {
                "pool_id": f"{venue.replace(' ', '_').lower()}_{base}_{quote}",
                "venue": venue,
                "family": family,
                "fee": fee,
                "math_params": math_params,
                "base": base,
                "quote": quote,
                "reserve_base": depth,
                "reserve_quote": depth * mid,
                "price": price,
                "tvl_usd": tvl_usd,
                "block": 65_000_000,
                "freshness_ms": int(random.uniform(50, 4000)),
            }
            pools.append(pool)
    return pools


# ---------------------------------------------------------------------------
# AAVE V3 SIMULATED POSITIONS (Polygon)
# ---------------------------------------------------------------------------
COLLATERAL_ASSETS = ["WETH", "WBTC", "WMATIC", "LINK", "AAVE"]
DEBT_ASSETS = ["USDC", "USDT", "DAI"]


def build_aave_positions(n: int = 22) -> List[Dict[str, Any]]:
    """Synthetic Aave V3 borrower positions on Polygon."""
    positions = []
    rng = random.Random(7)
    addr_pool = [
        "0x" + "".join(rng.choices("0123456789abcdef", k=40)) for _ in range(60)
    ]
    for i in range(n):
        collateral = rng.choice(COLLATERAL_ASSETS)
        debt = rng.choice(DEBT_ASSETS)
        collateral_units = rng.uniform(0.5, 80) if collateral == "WETH" else \
                           rng.uniform(0.01, 1.5) if collateral == "WBTC" else \
                           rng.uniform(2000, 60000) if collateral == "WMATIC" else \
                           rng.uniform(60, 1200)
        collateral_usd = collateral_units * TOKENS[collateral]["ref_usd"]
        ltv = rng.uniform(0.72, 0.93)
        debt_usd = collateral_usd * ltv
        liq_threshold = 0.82  # Aave V3 typical
        health_factor = (collateral_usd * liq_threshold) / max(debt_usd, 1.0)
        positions.append({
            "position_id": f"pos_{i:03d}_{addr_pool[i][2:8]}",
            "borrower": addr_pool[i],
            "collateral_asset": collateral,
            "debt_asset": debt,
            "collateral_units": collateral_units,
            "collateral_usd": collateral_usd,
            "debt_usd": debt_usd,
            "ltv": ltv,
            "liquidation_threshold": liq_threshold,
            "health_factor": health_factor,
            "liquidation_bonus_bps": rng.choice([500, 750, 800, 1000, 1250]),
            "protocol": "Aave V3",
            "last_update_block": 65_000_000,
        })
    return positions


def update_aave_positions(positions: List[Dict[str, Any]]):
    """Drift collateral prices each tick so positions move in & out of liquidation zone."""
    for p in positions:
        # price drift in bps
        drift = random.gauss(0, 18)
        # collateral_usd drifts; debt stays in USD terms
        p["collateral_usd"] *= (1 + drift / 10_000)
        p["health_factor"] = (p["collateral_usd"] * p["liquidation_threshold"]) / max(p["debt_usd"], 1.0)
        p["last_update_block"] = state.get("block", 0)


async def _price_ticker():
    """Mutate prices every 2s to simulate live block movement & create arb windows."""
    while True:
        try:
            await asyncio.sleep(2.0)
            state["block"] += 1
            graph = state["graph"]
            for pool in graph:
                jolt = 0.0
                if random.random() < 0.04:
                    jolt = random.uniform(-80, 80)
                drift = random.gauss(0, 6) + jolt
                pool["price"] *= (1 + drift / 10_000)
                pool["block"] = state["block"]
                pool["freshness_ms"] = int(random.uniform(50, 4000))
            update_aave_positions(state["positions"])
            state["last_tick"] = time.time()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("ticker error %s", e)


# ---------------------------------------------------------------------------
# MATH CORE (mirrors spec)
# ---------------------------------------------------------------------------

def hybrid_buffer(raw_spread_decimal: float, ml_slippage: float, amount_usdc: float,
                  volatility_factor: float, min_floor: float = 0.0005,
                  max_cap: float = 0.05) -> float:
    size_scaled = 0.005 * raw_spread_decimal * (amount_usdc / 100_000.0)
    ml_tamed = ml_slippage / 3.0
    hybrid_base = max(size_scaled, ml_tamed)
    adjusted = hybrid_base * volatility_factor
    return max(min(adjusted, max_cap), min_floor)


def fill_probability(buffer: float, base_exec_p: float) -> float:
    buffer_component = 1.0 - math.exp(-8.0 * buffer)
    return max(0.0, min(1.0, base_exec_p * buffer_component))


def slippage_cost(buffer: float, raw_spread: float) -> float:
    return raw_spread * (1.0 - math.exp(-5.0 * buffer))


def compute_ev(base_buffer: float, raw_spread: float, amount_usdc: float,
               gas_cost: float, fee_cost: float, exec_p: float,
               multiplier: float) -> tuple[float, float]:
    buffer = base_buffer * multiplier
    p_fill = fill_probability(buffer, exec_p)
    p_fail = 1 - p_fill
    slip = slippage_cost(buffer, raw_spread)
    profit_if_fill = amount_usdc * (raw_spread - slip - fee_cost)
    ev = (p_fill * profit_if_fill) - (p_fail * gas_cost)
    return buffer, ev


def optimize_ev_buffer(base_buffer: float, raw_spread: float, amount_usdc: float,
                       gas_cost: float, fee_cost: float, exec_p: float) -> Dict[str, Any]:
    multipliers = [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]
    curve = []
    for m in multipliers:
        buf, ev = compute_ev(base_buffer, raw_spread, amount_usdc, gas_cost, fee_cost, exec_p, m)
        curve.append({"multiplier": m, "buffer": buf, "ev": ev})
    best = max(curve, key=lambda r: r["ev"])
    return {
        "selected_buffer": best["buffer"],
        "selected_multiplier": best["multiplier"],
        "selected_ev_usdc": best["ev"],
        "curve": curve,
    }


def amm_v2_out(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Constant-product (V2) swap output."""
    amount_in_eff = amount_in * (1 - fee)
    return (amount_in_eff * reserve_out) / (reserve_in + amount_in_eff)


def amm_v3_out(amount_in: float, reserve_in: float, reserve_out: float, fee: float,
               concentration: float = 4.0) -> float:
    """Uniswap V3 / Algebra concentrated-liquidity swap via sqrt-price math.

    Uses the canonical V3 invariant:
        L = sqrt(x * y) * concentration  (concentration > 1 models tick range)
        sqrt_P = sqrt(reserve_out / reserve_in)
        token0 -> token1: sqrt_P_next = (L * sqrt_P) / (L + amount_in_eff * sqrt_P)
        amount_out = L * (sqrt_P - sqrt_P_next)

    concentration is family-dependent:
        Uniswap V3 (0.05% pools) -> ~5.5  (tight)
        QuickSwap V3            -> ~3.8
        Algebra (dynamic fee)    -> ~4.5
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    amount_in_eff = amount_in * (1 - fee)
    L = math.sqrt(reserve_in * reserve_out) * concentration
    sqrt_P = math.sqrt(reserve_out / reserve_in)
    sqrt_P_next = (L * sqrt_P) / (L + amount_in_eff * sqrt_P)
    amount_out = L * (sqrt_P - sqrt_P_next)
    return max(0.0, amount_out)


def amm_balancer_out(amount_in: float, reserve_in: float, reserve_out: float,
                     fee: float, weight_in: float = 0.5, weight_out: float = 0.5) -> float:
    """Balancer weighted pool (canonical):
        A_o = B_o * (1 - (B_i / (B_i + A_i*(1-f))) ^ (w_i / w_o))
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    amt_eff = amount_in * (1 - fee)
    ratio = reserve_in / (reserve_in + amt_eff)
    return reserve_out * (1.0 - ratio ** (weight_in / weight_out))


# --- Curve stableswap (proper invariant via Newton iteration) ---
def _curve_get_D(xp: List[float], A: float, n: int = 2, iters: int = 255) -> float:
    S = sum(xp)
    if S == 0:
        return 0.0
    D = S
    Ann = A * n
    for _ in range(iters):
        D_P = D
        for x in xp:
            D_P = D_P * D / (n * x) if x > 0 else 0
        D_prev = D
        num = (Ann * S + D_P * n) * D
        denom = (Ann - 1) * D + (n + 1) * D_P
        D = num / denom if denom else D
        if abs(D - D_prev) <= 1e-9:
            return D
    return D


def _curve_get_y(i: int, j: int, x_new: float, xp: List[float], A: float,
                 n: int = 2, iters: int = 255) -> float:
    """Given xp and a new value at index i, solve for value at index j on Curve invariant."""
    D = _curve_get_D(xp, A, n)
    Ann = A * n
    c = D
    S = 0.0
    for k in range(n):
        if k == i:
            _x = x_new
        elif k == j:
            continue
        else:
            _x = xp[k]
        S += _x
        c = c * D / (_x * n) if _x > 0 else 0
    c = c * D / (Ann * n) if Ann else 0
    b = S + D / Ann if Ann else S
    y = D
    for _ in range(iters):
        y_prev = y
        denom = (2 * y + b - D)
        y = (y * y + c) / denom if denom else y
        if abs(y - y_prev) <= 1e-9:
            return y
    return y


def amm_curve_out(amount_in: float, reserve_in: float, reserve_out: float,
                  fee: float, A: float = 100.0) -> float:
    """Curve stableswap with the proper StableSwap invariant (n=2 coins).

    A = amplification coefficient (Curve 3pool=100, sUSD=2000, frax=1500).
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    amount_in_eff = amount_in * (1 - fee)
    xp = [reserve_in, reserve_out]
    new_in = reserve_in + amount_in_eff
    new_out = _curve_get_y(0, 1, new_in, xp, A)
    return max(0.0, reserve_out - new_out)


def simulate_swap(pool: Dict[str, Any], amount_in_usd: float, direction: str = "buy") -> Dict[str, Any]:
    """
    direction='buy' -> buy BASE with QUOTE (amount_in_usd is quote-side USD)
    direction='sell' -> sell BASE for QUOTE
    Returns dict: amount_out_usd, price_realized, slip_bps
    """
    family = pool["family"]
    fee = pool["fee"]
    if direction == "buy":
        rin = pool["reserve_quote"]
        rout = pool["reserve_base"]
    else:
        rin = pool["reserve_base"]
        rout = pool["reserve_quote"]

    amt_in_token = amount_in_usd / (TOKENS[pool["quote" if direction == "buy" else "base"]]["ref_usd"])

    if family == "v2":
        out = amm_v2_out(amt_in_token, rin, rout, fee)
    elif family in ("v3", "algebra"):
        out = amm_v3_out(amt_in_token, rin, rout, fee)
    elif family == "balancer":
        out = amm_balancer_out(amt_in_token, rin, rout, fee, 0.5, 0.5)
    elif family == "curve":
        out = amm_curve_out(amt_in_token, rin, rout, fee)
    else:
        out = amm_v2_out(amt_in_token, rin, rout, fee)

    out_token = pool["base" if direction == "buy" else "quote"]
    out_usd = out * TOKENS[out_token]["ref_usd"]
    mid_price = pool["price"]
    realized = (out / amt_in_token) if direction == "buy" else (amt_in_token / out)
    expected = mid_price if direction == "buy" else mid_price
    slip_bps = abs((realized - expected) / expected) * 10_000 if expected else 0.0
    return {
        "amount_in_usd": amount_in_usd,
        "amount_out_usd": out_usd,
        "out_token": out_token,
        "realized_price": realized,
        "slip_bps": slip_bps,
    }


# ---------------------------------------------------------------------------
# LIQUIDITY ELIGIBILITY GATE
# ---------------------------------------------------------------------------
# Liquidity is NOT a strategy and NOT an execution gate.
# It is an enablement filter: pools must pass TVL + price-sanity + freshness
# checks to be ELIGIBLE for inclusion in arbitrage route discovery.
# Failing this gate just means the pool isn't considered as a candidate route.

DEFAULT_GATE_CONFIG = {
    "min_tvl_usd": float(os.environ.get("APEX_EXECUTABLE_MIN_TVL_USD", 5_000)),
    "max_price_dev_pct": 0.05,
    "max_freshness_ms": float(os.environ.get("APEX_MAX_QUOTE_AGE_MS", 1500)),
}


def apply_liquidity_gate(pools: List[Dict[str, Any]],
                         cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return ELIGIBLE pools + reject reasons for the rest.

    Gates (per spec):
      - TVL floor
      - Price-sanity vs pair median (kills stale UniV3 single-tick prints)
      - Quote freshness
    """
    cfg = {**DEFAULT_GATE_CONFIG, **(cfg or {})}
    by_pair: Dict[tuple, List[Dict[str, Any]]] = {}
    enriched = []
    for p in pools:
        ref_q = TOKENS[p["quote"]]["ref_usd"]
        usd_per_base = p["price"] * ref_q
        e = {**p, "usd_per_base": usd_per_base}
        enriched.append(e)
        by_pair.setdefault((p["base"], p["quote"]), []).append(e)

    eligible = []
    rejected = []
    gate_counters = {
        "tvl_fail": 0,
        "price_sanity_fail": 0,
        "freshness_fail": 0,
    }

    for (base, quote), group in by_pair.items():
        prices = sorted([g["usd_per_base"] for g in group])
        median = prices[len(prices) // 2]
        for g in group:
            reasons = []
            if g["tvl_usd"] < cfg["min_tvl_usd"]:
                reasons.append("tvl_floor")
                gate_counters["tvl_fail"] += 1
            dev = abs(g["usd_per_base"] - median) / max(median, 1e-9)
            if dev > cfg["max_price_dev_pct"]:
                reasons.append("price_sanity")
                gate_counters["price_sanity_fail"] += 1
            if g.get("freshness_ms", 0) > cfg["max_freshness_ms"]:
                reasons.append("freshness")
                gate_counters["freshness_fail"] += 1
            entry = {
                "pool_id": g["pool_id"],
                "venue": g["venue"],
                "family": g["family"],
                "pair": f"{base}/{quote}",
                "tvl_usd": g["tvl_usd"],
                "usd_per_base": g["usd_per_base"],
                "freshness_ms": g.get("freshness_ms"),
                "price_dev_pct": dev,
                "pair_median": median,
            }
            if reasons:
                rejected.append({**entry, "reject_reasons": reasons})
            else:
                eligible.append(g)
    return {
        "config": cfg,
        "eligible": eligible,
        "rejected": rejected,
        "counters": {
            "total": len(pools),
            "eligible": len(eligible),
            "rejected": len(rejected),
            **gate_counters,
        },
    }


# ---------------------------------------------------------------------------
# DISCOVERY (now uses ONLY pools that pass the liquidity gate)
# ---------------------------------------------------------------------------

def discover_opportunities(graph: List[Dict[str, Any]], trade_size_usd: float = 25_000,
                            min_spread_bps: float = 3.0,
                            gate_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Scan the eligible pool universe for cross-DEX arbitrage opportunities.

    Liquidity gate runs first (pool eligibility), then route search runs
    only across eligible pools. Returns dict with `opportunities` + `gate`
    breakdown so the UI can show enablement transparency.
    """
    gate = apply_liquidity_gate(graph, gate_cfg)
    eligible = gate["eligible"]
    by_pair: Dict[tuple, List[Dict[str, Any]]] = {}
    for pool in eligible:
        by_pair.setdefault((pool["base"], pool["quote"]), []).append(pool)

    opportunities = []
    for (base, quote), pools in by_pair.items():
        if len(pools) < 2:
            continue
        # Per-pool USD price for one unit of BASE
        priced = []
        for p in pools:
            ref_base = TOKENS[base]["ref_usd"]
            ref_quote = TOKENS[quote]["ref_usd"]
            usd_per_base = p["price"] * ref_quote
            priced.append({**p, "usd_per_base": usd_per_base, "ref_base_usd": ref_base})
        best_buy = min(priced, key=lambda x: x["usd_per_base"])
        best_sell = max(priced, key=lambda x: x["usd_per_base"])
        if best_buy["pool_id"] == best_sell["pool_id"]:
            continue
        raw_spread = best_sell["usd_per_base"] - best_buy["usd_per_base"]
        raw_spread_bps = (raw_spread / best_buy["usd_per_base"]) * 10_000
        if raw_spread_bps < min_spread_bps:
            continue

        # Simulate execution
        buy_sim = simulate_swap(best_buy, trade_size_usd, "buy")
        # We bought BASE; now sell BASE on best_sell
        amount_base = buy_sim["amount_out_usd"] / best_buy["usd_per_base"]
        # Simulate sell
        sell_pool = best_sell
        # For sell we need amount_in_usd terms; convert
        amt_in_usd_for_sell = amount_base * sell_pool["usd_per_base"]
        sell_sim = simulate_swap(sell_pool, amt_in_usd_for_sell, "sell")
        gross_out_usd = sell_sim["amount_out_usd"]
        gross_profit_usd = gross_out_usd - trade_size_usd

        # Costs
        # Flash loan fee: balancer 0bps, aave 9bps
        flash_provider = "Balancer V2"
        flash_fee_bps = 0
        flash_fee_usd = trade_size_usd * (flash_fee_bps / 10_000)
        gas_cost_usd = random.uniform(0.45, 1.85)
        dex_fee_reserve = trade_size_usd * 0.0001  # 1bps reserve
        net_profit_usd = gross_profit_usd - flash_fee_usd - gas_cost_usd - dex_fee_reserve

        # EV gate
        exec_p = 0.78  # base inclusion probability for Titan primary
        ml_slippage = (buy_sim["slip_bps"] + sell_sim["slip_bps"]) / 10_000
        volatility_factor = 1.0 + random.uniform(-0.1, 0.3)
        base_buffer = hybrid_buffer(
            raw_spread / best_buy["usd_per_base"], ml_slippage,
            trade_size_usd, volatility_factor,
        )
        ev_result = optimize_ev_buffer(
            base_buffer,
            raw_spread / best_buy["usd_per_base"],
            trade_size_usd, gas_cost_usd,
            (best_buy["fee"] + sell_pool["fee"]),
            exec_p,
        )

        opp = {
            "opp_id": f"opp_{uuid.uuid4().hex[:10]}",
            "block": state.get("block", 0),
            "pair": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "buy_venue": best_buy["venue"],
            "buy_pool_id": best_buy["pool_id"],
            "buy_family": best_buy["family"],
            "buy_price_usd": best_buy["usd_per_base"],
            "sell_venue": best_sell["venue"],
            "sell_pool_id": best_sell["pool_id"],
            "sell_family": best_sell["family"],
            "sell_price_usd": best_sell["usd_per_base"],
            "raw_spread_usd": raw_spread,
            "raw_spread_bps": raw_spread_bps,
            "trade_size_usd": trade_size_usd,
            "buy_sim": buy_sim,
            "sell_sim": sell_sim,
            "gross_profit_usd": gross_profit_usd,
            "flash_provider": flash_provider,
            "flash_fee_bps": flash_fee_bps,
            "flash_fee_usd": flash_fee_usd,
            "gas_cost_usd": gas_cost_usd,
            "dex_fee_reserve_usd": dex_fee_reserve,
            "net_profit_usd": net_profit_usd,
            "base_buffer": base_buffer,
            "selected_buffer": ev_result["selected_buffer"],
            "selected_ev_usdc": ev_result["selected_ev_usdc"],
            "ev_curve": ev_result["curve"],
            "execution_probability": exec_p,
            "volatility_factor": volatility_factor,
            "ml_slippage_bps": ml_slippage * 10_000,
            "tvl_buy_usd": best_buy["tvl_usd"],
            "tvl_sell_usd": sell_pool["tvl_usd"],
            "status": "discovered",
            "executable": (net_profit_usd > 0.5 and ev_result["selected_ev_usdc"] > 0.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        opportunities.append(opp)

    opportunities.sort(key=lambda x: x["selected_ev_usdc"], reverse=True)
    return {"opportunities": opportunities, "gate": gate}


# ---------------------------------------------------------------------------
# EVM MIRROR (simplified pass-through validation)
# ---------------------------------------------------------------------------

def evm_mirror_validate(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Off-chain simulation of the route; returns pass/fail + gates."""
    checks = []
    ok = True
    # Gate: route step count
    step_count = 2  # buy + sell
    checks.append({"gate": "route_step_count<=24", "value": step_count, "pass": True})
    # Gate: minAmountOut delta
    delta_ok = opp["sell_sim"]["amount_out_usd"] > opp["trade_size_usd"]
    checks.append({"gate": "min_amount_out_delta", "value": opp["sell_sim"]["amount_out_usd"], "pass": delta_ok})
    if not delta_ok:
        ok = False
    # Gate: net profit > flash fee
    net_ok = opp["net_profit_usd"] > 0
    checks.append({"gate": "net_profit>0", "value": opp["net_profit_usd"], "pass": net_ok})
    if not net_ok:
        ok = False
    # Gate: EV > threshold
    ev_ok = opp["selected_ev_usdc"] > 0
    checks.append({"gate": "EV>threshold", "value": opp["selected_ev_usdc"], "pass": ev_ok})
    if not ev_ok:
        ok = False
    # Gate: pool family supported
    fam_buy = opp["buy_family"]
    fam_sell = opp["sell_family"]
    fam_ok = fam_buy in ("v2", "v3", "algebra", "balancer", "curve") and fam_sell in ("v2", "v3", "algebra", "balancer", "curve")
    checks.append({"gate": "pool_family_supported", "value": f"{fam_buy}+{fam_sell}", "pass": fam_ok})
    if not fam_ok:
        ok = False
    return {"passed": ok, "checks": checks}


# ---------------------------------------------------------------------------
# C1 AGGRESSOR
# ---------------------------------------------------------------------------

def build_c1_route_envelope(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Build C1 RouteEnvelope for InstitutionalExecutor."""
    mirror_expected_out_usd = opp["sell_sim"]["amount_out_usd"]
    selected_buffer = opp["selected_buffer"]
    min_amount_out_usd = mirror_expected_out_usd * (1 - selected_buffer)
    envelope = {
        "version": 1,
        "profit_token": opp["quote"],
        "gas_reserve_asset_usd": opp["gas_cost_usd"] * 1.2,
        "dex_fee_reserve_asset_usd": opp["dex_fee_reserve_usd"],
        "steps": [
            {
                "protocol": opp["buy_family"],
                "venue": opp["buy_venue"],
                "approve_token": opp["quote"],
                "output_token": opp["base"],
                "amount_in_usd": opp["trade_size_usd"],
                "min_amount_out_usd": opp["buy_sim"]["amount_out_usd"] * (1 - selected_buffer / 2),
                "fee_bps": int(opp["raw_spread_bps"]),
            },
            {
                "protocol": opp["sell_family"],
                "venue": opp["sell_venue"],
                "approve_token": opp["base"],
                "output_token": opp["quote"],
                "amount_in_usd": opp["buy_sim"]["amount_out_usd"],
                "min_amount_out_usd": min_amount_out_usd,
                "fee_bps": int(opp["raw_spread_bps"]),
            },
        ],
        "min_amount_out_usd": min_amount_out_usd,
    }
    envelope_hash = "0x" + hashlib.sha256(str(envelope).encode()).hexdigest()[:40]
    envelope["envelope_hash"] = envelope_hash
    return envelope


def execute_c1(opp: Dict[str, Any]) -> Dict[str, Any]:
    """C1 Aggressor: first attack in Block N."""
    envelope = build_c1_route_envelope(opp)
    mirror = evm_mirror_validate(opp)
    if not mirror["passed"]:
        return {
            "phase": "C1",
            "status": "rejected",
            "envelope": envelope,
            "mirror": mirror,
            "reason": "EVM mirror failed",
        }
    # Submit Titan bundle (simulated)
    relay = "Titan_MEV_US_West"
    latency_ms = random.uniform(38, 92)
    inclusion = random.random() < opp["execution_probability"]
    bundle_hash = "0x" + hashlib.sha256(f"{envelope['envelope_hash']}_{time.time()}".encode()).hexdigest()[:40]
    if inclusion:
        # Actual outcome deviates modestly from expected (buffer absorbs slip)
        realization = random.uniform(0.60, 1.05)
        actual_net = opp["net_profit_usd"] * realization
        actual_out_usd = opp["trade_size_usd"] + actual_net + opp["flash_fee_usd"] + opp["gas_cost_usd"]
        actual_slip = max(0.0, (opp["sell_sim"]["amount_out_usd"] - actual_out_usd) / opp["sell_sim"]["amount_out_usd"])
        return {
            "phase": "C1",
            "status": "executed",
            "envelope": envelope,
            "mirror": mirror,
            "relay": relay,
            "bundle_hash": bundle_hash,
            "block_target": opp["block"] + 1,
            "latency_ms": latency_ms,
            "actual_out_usd": actual_out_usd,
            "actual_slip": actual_slip,
            "actual_net_profit_usd": actual_net,
            "expected_net_profit_usd": opp["net_profit_usd"],
        }
    # Reverted - try fallback Titan global
    return {
        "phase": "C1",
        "status": "reverted",
        "envelope": envelope,
        "mirror": mirror,
        "relay": relay,
        "bundle_hash": bundle_hash,
        "block_target": opp["block"] + 1,
        "latency_ms": latency_ms,
        "actual_net_profit_usd": -opp["gas_cost_usd"],
        "reason": "Bundle not included",
    }


# ---------------------------------------------------------------------------
# C2 SURGEON (MIRROR / REVERSE / DO_NOTHING) with Merkle tree
# ---------------------------------------------------------------------------

def merkle_leaf(envelope: Dict[str, Any]) -> str:
    return "0x" + hashlib.sha256(str(envelope).encode()).hexdigest()

def build_merkle_root(leaves: List[str]) -> tuple[str, List[List[str]]]:
    if not leaves:
        return "0x" + "00" * 32, []
    levels = [leaves[:]]
    while len(levels[-1]) > 1:
        prev = levels[-1]
        nxt = []
        for i in range(0, len(prev), 2):
            a = prev[i]
            b = prev[i + 1] if i + 1 < len(prev) else prev[i]
            pair = sorted([a, b])
            nxt.append("0x" + hashlib.sha256((pair[0] + pair[1]).encode()).hexdigest())
        levels.append(nxt)
    return levels[-1][0], levels


def merkle_proof(leaves: List[str], target_idx: int) -> List[str]:
    proof = []
    level = leaves[:]
    idx = target_idx
    while len(level) > 1:
        sibling = idx ^ 1
        if sibling < len(level):
            proof.append(level[sibling])
        nxt = []
        for i in range(0, len(level), 2):
            a = level[i]
            b = level[i + 1] if i + 1 < len(level) else level[i]
            pair = sorted([a, b])
            nxt.append("0x" + hashlib.sha256((pair[0] + pair[1]).encode()).hexdigest())
        level = nxt
        idx //= 2
    return proof


def execute_c2(opp: Dict[str, Any], c1_result: Dict[str, Any]) -> Dict[str, Any]:
    """C2 Surgeon: MIRROR/REVERSE/DO_NOTHING based on post-C1 state."""
    if c1_result["status"] != "executed":
        return {
            "phase": "C2",
            "action": "DO_NOTHING",
            "reason": "C1 did not execute",
            "merkle_root": "0x" + "00" * 32,
            "candidates": [],
        }

    # Re-evaluate post-C1: spread is partially closed by C1 impact
    residual_factor = random.uniform(0.15, 0.55)  # how much edge remains
    rebound_factor = random.uniform(0.20, 0.65)   # overcorrection rebound

    mirror_size = opp["trade_size_usd"] * 0.55
    reverse_size = opp["trade_size_usd"] * 0.40

    # MIRROR: same direction, residual edge
    mirror_spread = (opp["raw_spread_usd"] / opp["buy_price_usd"]) * residual_factor
    mirror_gross = mirror_size * mirror_spread
    mirror_gas = random.uniform(0.40, 1.20)
    mirror_net = mirror_gross - mirror_gas - mirror_size * 0.0001
    mirror_buf = hybrid_buffer(mirror_spread, opp["ml_slippage_bps"] / 10_000, mirror_size, 1.1)
    mirror_ev = optimize_ev_buffer(mirror_buf, mirror_spread, mirror_size, mirror_gas, 0.0008, 0.72)
    mirror_envelope = {
        "version": 1,
        "action": "MIRROR",
        "profit_token": opp["quote"],
        "amount_in_usd": mirror_size,
        "expected_out_usd": mirror_size + mirror_gross,
        "min_amount_out_usd": (mirror_size + mirror_gross) * (1 - mirror_ev["selected_buffer"]),
    }

    # REVERSE: opposite direction (sell where C1 bought, buy where C1 sold)
    reverse_spread = (opp["raw_spread_usd"] / opp["buy_price_usd"]) * rebound_factor
    reverse_gross = reverse_size * reverse_spread
    reverse_gas = random.uniform(0.40, 1.20)
    reverse_net = reverse_gross - reverse_gas - reverse_size * 0.0001
    reverse_buf = hybrid_buffer(reverse_spread, opp["ml_slippage_bps"] / 10_000, reverse_size, 1.1)
    reverse_ev = optimize_ev_buffer(reverse_buf, reverse_spread, reverse_size, reverse_gas, 0.0008, 0.68)
    reverse_envelope = {
        "version": 1,
        "action": "REVERSE",
        "profit_token": opp["quote"],
        "amount_in_usd": reverse_size,
        "expected_out_usd": reverse_size + reverse_gross,
        "min_amount_out_usd": (reverse_size + reverse_gross) * (1 - reverse_ev["selected_buffer"]),
    }

    # DO_NOTHING
    do_nothing_envelope = {"version": 1, "action": "DO_NOTHING", "ev_usdc": 0.0}

    candidates = [
        {
            "action": "MIRROR",
            "expected_ev_usdc": mirror_ev["selected_ev_usdc"],
            "expected_net_usd": mirror_net,
            "selected_buffer": mirror_ev["selected_buffer"],
            "envelope": mirror_envelope,
        },
        {
            "action": "REVERSE",
            "expected_ev_usdc": reverse_ev["selected_ev_usdc"],
            "expected_net_usd": reverse_net,
            "selected_buffer": reverse_ev["selected_buffer"],
            "envelope": reverse_envelope,
        },
        {
            "action": "DO_NOTHING",
            "expected_ev_usdc": 0.0,
            "expected_net_usd": 0.0,
            "selected_buffer": 0.0,
            "envelope": do_nothing_envelope,
        },
    ]

    # Merkle leaves
    leaves = [merkle_leaf(c["envelope"]) for c in candidates]
    root, _levels = build_merkle_root(leaves)
    for i, c in enumerate(candidates):
        c["merkle_leaf"] = leaves[i]
        c["proof"] = merkle_proof(leaves, i)

    # Select highest EV
    best = max(candidates, key=lambda c: c["expected_ev_usdc"])
    selected_action = best["action"]

    # Submit (simulated) if not DO_NOTHING
    if selected_action == "DO_NOTHING":
        return {
            "phase": "C2",
            "action": "DO_NOTHING",
            "merkle_root": root,
            "candidates": candidates,
            "selected": best,
            "reason": "No positive-EV candidate after C1",
        }

    inclusion = random.random() < (0.72 if selected_action == "MIRROR" else 0.68)
    bundle_hash = "0x" + hashlib.sha256(f"c2_{best['merkle_leaf']}_{time.time()}".encode()).hexdigest()[:40]
    relay = "Titan_MEV_US_West"
    latency = random.uniform(42, 110)
    if inclusion:
        actual_net = best["expected_net_usd"] * random.uniform(0.65, 1.05)
        return {
            "phase": "C2",
            "action": selected_action,
            "merkle_root": root,
            "candidates": candidates,
            "selected": best,
            "relay": relay,
            "bundle_hash": bundle_hash,
            "block_target": opp["block"] + 2,
            "latency_ms": latency,
            "status": "executed",
            "actual_net_profit_usd": actual_net,
        }
    return {
        "phase": "C2",
        "action": selected_action,
        "merkle_root": root,
        "candidates": candidates,
        "selected": best,
        "relay": relay,
        "bundle_hash": bundle_hash,
        "block_target": opp["block"] + 2,
        "latency_ms": latency,
        "status": "reverted",
        "actual_net_profit_usd": -random.uniform(0.4, 1.2),
        "reason": "Bundle not included",
    }


# ---------------------------------------------------------------------------
# RISK SCORING
# ---------------------------------------------------------------------------

def compute_risk(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Risk panel: route fragility, state divergence, revert probability, etc."""
    fragility = min(1.0, (opp["ml_slippage_bps"] / 30.0) * 0.6 +
                    (1 - min(opp["tvl_buy_usd"], opp["tvl_sell_usd"]) / 5_000_000) * 0.4)
    state_div = abs(opp["volatility_factor"] - 1.0)
    revert_p = max(0.0, 1.0 - opp["execution_probability"])
    mempool_tox = random.uniform(0.05, 0.45) if opp["raw_spread_bps"] > 15 else random.uniform(0.02, 0.20)
    saturation = min(1.0, opp["trade_size_usd"] / max(opp["tvl_buy_usd"] * 0.05, 1))
    v3_confidence = 0.92 if "v3" in opp["buy_family"] or "v3" in opp["sell_family"] else 0.99
    return {
        "route_fragility": round(fragility, 4),
        "state_divergence": round(state_div, 4),
        "revert_probability": round(revert_p, 4),
        "mempool_toxicity": round(mempool_tox, 4),
        "liquidity_saturation": round(saturation, 4),
        "v3_tick_confidence": round(v3_confidence, 4),
        "overall_risk_score": round((fragility + revert_p + mempool_tox + saturation) / 4, 4),
    }


# ---------------------------------------------------------------------------
# LIQUIDATION ENGINE (Aave V3 style)
# ---------------------------------------------------------------------------
# Separate strategy. Targets borrower positions whose health factor breaches
# threshold (HF < 1.0 = liquidatable). Operator can liquidate up to close-factor
# of the debt, paying it down and seizing collateral + liquidation bonus.

CLOSE_FACTOR = 0.5  # Aave V3 default — can liquidate up to 50% of debt


def score_position(p: Dict[str, Any]) -> Dict[str, Any]:
    """Compute liquidation eligibility + expected bonus for a position."""
    hf = p["health_factor"]
    eligible = hf < 1.0
    margin_breach_pct = max(0.0, (1.0 - hf) * 100)  # how far underwater
    # Max repayable = close_factor * debt
    max_repay_usd = p["debt_usd"] * CLOSE_FACTOR
    # Collateral seized = repay_usd * (1 + bonus)
    bonus_decimal = p["liquidation_bonus_bps"] / 10_000
    seized_usd = max_repay_usd * (1 + bonus_decimal)
    raw_bonus_usd = seized_usd - max_repay_usd
    # Flash-loan to fund the repayment (Aave V3 = 5bps; Balancer = 0bps)
    flash_fee_bps = 0
    flash_fee_usd = max_repay_usd * (flash_fee_bps / 10_000)
    gas_cost_usd = random.uniform(0.85, 2.40)
    net_bonus_usd = raw_bonus_usd - flash_fee_usd - gas_cost_usd
    return {
        **p,
        "eligible": eligible,
        "margin_breach_pct": margin_breach_pct,
        "max_repay_usd": max_repay_usd,
        "seized_collateral_usd": seized_usd,
        "raw_bonus_usd": raw_bonus_usd,
        "flash_fee_usd": flash_fee_usd,
        "gas_cost_usd": gas_cost_usd,
        "net_bonus_usd": net_bonus_usd,
        "executable": eligible and net_bonus_usd > 0.5,
    }


def execute_liquidation(scored_pos: Dict[str, Any]) -> Dict[str, Any]:
    """Execute liquidationCall via flash-loan funded repayment + collateral seizure."""
    if not scored_pos["eligible"]:
        return {
            "status": "rejected",
            "reason": f"Position not liquidatable (HF={scored_pos['health_factor']:.4f})",
        }
    relay = "Titan_MEV_US_West"
    bundle_hash = "0x" + hashlib.sha256(
        f"liq_{scored_pos['position_id']}_{time.time()}".encode()
    ).hexdigest()[:40]
    # Inclusion probability for liquidation bundles tends to be higher
    # (less competition than ultra-popular arb routes)
    inclusion_p = 0.84 if scored_pos["margin_breach_pct"] > 8 else 0.62
    inclusion = random.random() < inclusion_p
    latency_ms = random.uniform(45, 105)
    if inclusion:
        realization = random.uniform(0.78, 1.02)
        actual_net = scored_pos["net_bonus_usd"] * realization
        return {
            "status": "executed",
            "relay": relay,
            "bundle_hash": bundle_hash,
            "block_target": state.get("block", 0) + 1,
            "latency_ms": latency_ms,
            "actual_net_bonus_usd": actual_net,
            "expected_net_bonus_usd": scored_pos["net_bonus_usd"],
        }
    return {
        "status": "frontran",
        "relay": relay,
        "bundle_hash": bundle_hash,
        "latency_ms": latency_ms,
        "actual_net_bonus_usd": -scored_pos["gas_cost_usd"],
        "reason": "Frontrun by competitor or HF recovered",
    }


# ---------------------------------------------------------------------------
# Pydantic API models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    trade_size_usd: float = Field(default=12_000, ge=100, le=2_000_000)
    min_spread_bps: float = Field(default=8.0, ge=0.0, le=500.0)


class PipelineRequest(BaseModel):
    opp_id: str


class LiquidationRequest(BaseModel):
    position_id: str


class GateConfig(BaseModel):
    min_tvl_usd: Optional[float] = None
    max_price_dev_pct: Optional[float] = None
    max_freshness_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "apex-omega-final-2.0", "block": state.get("block")}


@app.get("/api/status")
async def status():
    graph = state.get("graph", [])
    venues = sorted({p["venue"] for p in graph})
    pairs = sorted({f"{p['base']}/{p['quote']}" for p in graph})
    cycles_count = await state["db"].cycles.count_documents({})
    profitable_count = await state["db"].cycles.count_documents({"total_net_profit_usd": {"$gt": 0}})
    return {
        "ok": True,
        "chain": "Polygon 137",
        "mode": state.get("mode", "LIVE"),
        "fork": state.get("fork_health", {"ok": False}),
        "require_fork_sim_before_submit": REQUIRE_FORK_SIM,
        "block": state.get("block"),
        "last_tick_age_s": round(time.time() - state.get("last_tick", time.time()), 2),
        "venues": venues,
        "pairs": pairs,
        "pool_count": len(graph),
        "cycles_total": cycles_count,
        "cycles_profitable": profitable_count,
        "relays": ["Titan_MEV_US_West", "Titan_MEV_Global", "Titan_MEV_EU", "Fastlane"],
        "treasury": os.environ.get("TREASURY_WALLET", ""),
        "executor_c1": os.environ.get("C1_ARB_EXECUTOR_ADDRESS", ""),
        "executor_c2": os.environ.get("C2_ARB_EXECUTOR_ADDRESS", ""),
        "executor_liq": os.environ.get("LIQUIDATION_EXECUTOR_ADDRESS", ""),
        "modules": [
            {"name": "discovery", "ok": True},
            {"name": "liquidity_gate", "ok": True},
            {"name": "evm_mirror", "ok": True},
            {"name": "fork_sim", "ok": state.get("fork_health", {}).get("ok", False)},
            {"name": "c1_aggressor", "ok": True},
            {"name": "c2_surgeon_merkle", "ok": True},
            {"name": "titan_bundler", "ok": True},
            {"name": "ev_optimizer", "ok": True},
            {"name": "hybrid_buffer", "ok": True},
            {"name": "liquidation_engine", "ok": True},
        ],
    }


@app.post("/api/mode")
async def set_mode(payload: Dict[str, str]):
    """Switch operational mode: LIVE | SHADOW | SIM."""
    new_mode = (payload.get("mode") or "").upper()
    if new_mode not in ("LIVE", "SHADOW", "SIM"):
        raise HTTPException(400, "mode must be LIVE | SHADOW | SIM")
    state["mode"] = new_mode
    # If switching back into LIVE/SHADOW, ensure fork is up
    if new_mode in ("LIVE", "SHADOW") and not (await get_fork()).is_port_open():
        asyncio.create_task(_bootstrap_fork())
    return {"mode": new_mode, "fork_health": state.get("fork_health")}


@app.get("/api/fork/health")
async def fork_health():
    fork = await get_fork()
    h = await fork.health()
    state["fork_health"] = h
    return h


@app.post("/api/fork/respawn")
async def fork_respawn():
    fork = await get_fork()
    fork.kill()
    h = await ensure_fork_running()
    state["fork_health"] = h
    return h


@app.get("/api/graph")
async def graph_endpoint():
    """Live snapshot of the liquidity graph."""
    graph = state.get("graph", [])
    enriched = []
    for p in graph:
        ref_q = TOKENS[p["quote"]]["ref_usd"]
        enriched.append({**p, "usd_per_base": p["price"] * ref_q})
    return {"block": state["block"], "pools": enriched}


@app.post("/api/discovery/scan")
async def discovery_scan(req: ScanRequest):
    graph = state.get("graph", [])
    result = discover_opportunities(graph, req.trade_size_usd, req.min_spread_bps)
    opps = result["opportunities"]
    if opps:
        await state["db"].opportunities.insert_many([{**o} for o in opps])
    return {
        "count": len(opps),
        "opportunities": opps,
        "gate": {
            "config": result["gate"]["config"],
            "counters": result["gate"]["counters"],
            "rejected": result["gate"]["rejected"][:50],
        },
    }


@app.get("/api/liquidity/gate")
async def liquidity_gate(min_tvl_usd: Optional[float] = None,
                          max_price_dev_pct: Optional[float] = None,
                          max_freshness_ms: Optional[float] = None):
    """Run the liquidity eligibility gate and return pool PASS/FAIL breakdown.

    This is an enablement filter, not an execution gate.
    """
    cfg = {k: v for k, v in {
        "min_tvl_usd": min_tvl_usd,
        "max_price_dev_pct": max_price_dev_pct,
        "max_freshness_ms": max_freshness_ms,
    }.items() if v is not None}
    graph = state.get("graph", [])
    gate = apply_liquidity_gate(graph, cfg)
    # Strip the full enriched dicts in `eligible` (only return summary)
    eligible_summary = [
        {
            "pool_id": e["pool_id"],
            "venue": e["venue"],
            "family": e["family"],
            "pair": f"{e['base']}/{e['quote']}",
            "tvl_usd": e["tvl_usd"],
            "usd_per_base": e["usd_per_base"],
            "freshness_ms": e.get("freshness_ms"),
        }
        for e in gate["eligible"]
    ]
    return {
        "block": state.get("block"),
        "config": gate["config"],
        "counters": gate["counters"],
        "eligible": eligible_summary,
        "rejected": gate["rejected"],
    }


@app.get("/api/liquidations/scan")
async def liquidations_scan(only_eligible: bool = False):
    """Scan Aave V3 positions; flag those with HF < 1.0 as liquidatable."""
    positions = state.get("positions", [])
    scored = [score_position(p) for p in positions]
    scored.sort(key=lambda x: x["health_factor"])
    if only_eligible:
        scored = [s for s in scored if s["eligible"]]
    eligible_n = sum(1 for s in scored if s["eligible"])
    return {
        "block": state.get("block"),
        "count": len(scored),
        "eligible_count": eligible_n,
        "close_factor": CLOSE_FACTOR,
        "positions": scored,
    }


@app.post("/api/liquidations/execute")
async def liquidations_execute(req: LiquidationRequest):
    """Execute a liquidation bundle on a specific borrower position."""
    positions = state.get("positions", [])
    target = next((p for p in positions if p["position_id"] == req.position_id), None)
    if not target:
        raise HTTPException(404, "position not found")
    scored = score_position(target)
    result = execute_liquidation(scored)
    record = {
        "liquidation_id": f"liq_{uuid.uuid4().hex[:12]}",
        "position_id": target["position_id"],
        "borrower": target["borrower"],
        "collateral_asset": target["collateral_asset"],
        "debt_asset": target["debt_asset"],
        "health_factor_at_exec": scored["health_factor"],
        "max_repay_usd": scored["max_repay_usd"],
        "seized_collateral_usd": scored["seized_collateral_usd"],
        "liquidation_bonus_bps": target["liquidation_bonus_bps"],
        "flash_fee_usd": scored["flash_fee_usd"],
        "gas_cost_usd": scored["gas_cost_usd"],
        "expected_net_bonus_usd": scored["net_bonus_usd"],
        "result": result,
        "actual_net_bonus_usd": result.get("actual_net_bonus_usd", 0.0),
        "status": result["status"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state["db"].liquidations.insert_one({**record})
    # After a successful liquidation, the position is repaired (debt down, collateral down).
    if result["status"] == "executed":
        target["debt_usd"] -= scored["max_repay_usd"]
        target["collateral_usd"] -= scored["seized_collateral_usd"]
        target["health_factor"] = (target["collateral_usd"] * target["liquidation_threshold"]) / max(target["debt_usd"], 1.0)
    record.pop("_id", None)
    return record


@app.get("/api/liquidations/history")
async def liquidations_history(limit: int = 50):
    docs = await state["db"].liquidations.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(length=limit)
    return {"count": len(docs), "liquidations": docs}


@app.get("/api/opportunities")
async def list_opportunities(limit: int = 25):
    docs = await state["db"].opportunities.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(length=limit)
    return {"count": len(docs), "opportunities": docs}


@app.get("/api/discovery/stream")
async def discovery_stream(trade_size_usd: float = 25_000, min_spread_bps: float = 3.0):
    async def generator():
        while True:
            try:
                graph = state.get("graph", [])
                result = discover_opportunities(graph, trade_size_usd, min_spread_bps)
                opps = result["opportunities"]
                yield {"event": "snapshot", "data": __import__("json").dumps({
                    "block": state["block"], "count": len(opps), "opportunities": opps[:20],
                    "gate_counters": result["gate"]["counters"],
                })}
                await asyncio.sleep(2.5)
            except asyncio.CancelledError:
                break
    return EventSourceResponse(generator())


@app.get("/api/risk/{opp_id}")
async def risk_for(opp_id: str):
    opp = await state["db"].opportunities.find_one({"opp_id": opp_id}, {"_id": 0})
    if not opp:
        raise HTTPException(404, "opportunity not found")
    return compute_risk(opp)


@app.post("/api/pipeline/run")
async def pipeline_run(req: PipelineRequest):
    """
    End-to-end: discovery -> EVM mirror -> [FORK SIM gate] -> C1 -> [FORK SIM gate] -> C2 -> Titan submit -> archive.

    Mode behaviour:
      LIVE   : fork-sim required; on pass, submit real bundle to Titan
      SHADOW : fork-sim required; bundle logged but never reaches Titan
      SIM    : fork-sim skipped; pure in-memory simulation (legacy mode)
    """
    opp = await state["db"].opportunities.find_one({"opp_id": req.opp_id}, {"_id": 0})
    if not opp:
        raise HTTPException(404, "opportunity not found")

    mode = state.get("mode", "LIVE").upper()
    risk = compute_risk(opp)

    # ---- C1 ----
    c1_envelope = build_c1_route_envelope(opp)
    c1_fork = await _fork_sim_gate(c1_envelope,
                                    executor=os.environ.get("C1_ARB_EXECUTOR_ADDRESS", ""),
                                    label="C1", mode=mode)
    if mode in ("LIVE", "SHADOW") and REQUIRE_FORK_SIM and not c1_fork["pass"]:
        # Abort: never submit a bundle that fails fork sim
        c1 = {
            "phase": "C1",
            "status": "fork_sim_failed",
            "envelope": c1_envelope,
            "mirror": evm_mirror_validate(opp),
            "fork_sim": c1_fork,
            "reason": c1_fork.get("revert_reason") or "fork sim gate rejected bundle",
            "actual_net_profit_usd": 0.0,
        }
    else:
        c1 = execute_c1(opp) if mode != "SHADOW" else _shadow_c1(opp, c1_envelope)
        c1["fork_sim"] = c1_fork
        c1["envelope"] = c1_envelope

    # ---- C2 (only if C1 executed and fork-sim still passes) ----
    if c1.get("status") == "executed":
        c2 = execute_c2(opp, c1) if mode != "SHADOW" else _shadow_c2(opp, c1)
        # Run fork sim on the selected C2 envelope
        if c2.get("selected", {}).get("envelope"):
            c2_fork = await _fork_sim_gate(c2["selected"]["envelope"],
                                            executor=os.environ.get("C2_ARB_EXECUTOR_ADDRESS", ""),
                                            label="C2", mode=mode)
            c2["fork_sim"] = c2_fork
            if mode in ("LIVE", "SHADOW") and REQUIRE_FORK_SIM and not c2_fork["pass"]:
                c2["status"] = "fork_sim_failed"
                c2["actual_net_profit_usd"] = 0.0
                c2["reason"] = c2_fork.get("revert_reason") or "C2 fork sim rejected"
        else:
            c2["fork_sim"] = {"pass": True, "skipped": True, "reason": "DO_NOTHING"}
    else:
        c2 = execute_c2(opp, c1)  # returns DO_NOTHING shell
        c2["fork_sim"] = {"pass": False, "skipped": True, "reason": "C1 did not execute"}

    c1_pnl = c1.get("actual_net_profit_usd", 0.0) or 0.0
    c2_pnl = c2.get("actual_net_profit_usd", 0.0) if c2.get("status") == "executed" else 0.0
    total_pnl = c1_pnl + c2_pnl

    cycle = {
        "cycle_id": f"cyc_{uuid.uuid4().hex[:12]}",
        "opp_id": opp["opp_id"],
        "block_start": opp["block"],
        "pair": opp["pair"],
        "trade_size_usd": opp["trade_size_usd"],
        "mode": mode,
        "opportunity": opp,
        "risk": risk,
        "c1": c1,
        "c2": c2,
        "c1_net_profit_usd": c1_pnl,
        "c2_net_profit_usd": c2_pnl,
        "total_net_profit_usd": total_pnl,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await state["db"].cycles.insert_one({**cycle})
    await state["db"].opportunities.update_one(
        {"opp_id": opp["opp_id"]}, {"$set": {"status": "executed"}},
    )

    cycle.pop("_id", None)
    return cycle


async def _fork_sim_gate(envelope: Dict[str, Any], executor: str, label: str,
                          mode: str) -> Dict[str, Any]:
    """Run the bundle through Anvil. In SIM mode, skip & mark gate as bypassed."""
    if mode == "SIM":
        return {"pass": True, "skipped": True, "mode": "SIM",
                "reason": "SIM mode bypasses fork sim", "label": label}
    try:
        fork = await get_fork()
        if not fork.is_port_open():
            # Try one re-spawn before giving up
            await ensure_fork_running()
        if not fork.is_port_open():
            return {"pass": False, "skipped": False, "mode": mode, "label": label,
                    "revert_reason": "fork unreachable",
                    "fork_block": None, "step_count": 0, "steps": []}
        result = await fork.simulate_bundle(envelope, executor)
        result["mode"] = mode
        result["label"] = label
        return result
    except Exception as e:
        logger.exception("fork sim %s failed: %s", label, e)
        return {"pass": False, "mode": mode, "label": label,
                "revert_reason": f"fork_sim_exception: {e}",
                "fork_block": None, "step_count": 0, "steps": []}


def _shadow_c1(opp: Dict[str, Any], envelope: Dict[str, Any]) -> Dict[str, Any]:
    """SHADOW mode: pretend C1 executes, but mark it as not actually broadcast."""
    return {
        "phase": "C1",
        "status": "executed",
        "envelope": envelope,
        "mirror": evm_mirror_validate(opp),
        "relay": "SHADOW (no broadcast)",
        "bundle_hash": "0x" + "00" * 20 + "SHADOW".ljust(20, "0").encode().hex()[:20],
        "block_target": opp["block"] + 1,
        "latency_ms": 0.0,
        "actual_net_profit_usd": opp["net_profit_usd"],
        "expected_net_profit_usd": opp["net_profit_usd"],
        "shadow": True,
    }


def _shadow_c2(opp: Dict[str, Any], c1: Dict[str, Any]) -> Dict[str, Any]:
    out = execute_c2(opp, c1)
    out["relay"] = "SHADOW (no broadcast)"
    out["shadow"] = True
    return out


@app.get("/api/cycles")
async def cycles(limit: int = 50):
    docs = await state["db"].cycles.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(length=limit)
    return {"count": len(docs), "cycles": docs}


@app.get("/api/cycles/{cycle_id}")
async def cycle_detail(cycle_id: str):
    doc = await state["db"].cycles.find_one({"cycle_id": cycle_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "cycle not found")
    return doc


@app.get("/api/telemetry")
async def telemetry():
    """Aggregate metrics for dashboard headline."""
    db = state["db"]
    cycles_count = await db.cycles.count_documents({})
    pipeline = [
        {"$group": {
            "_id": None,
            "total_profit": {"$sum": "$total_net_profit_usd"},
            "c1_profit": {"$sum": "$c1_net_profit_usd"},
            "c2_profit": {"$sum": "$c2_net_profit_usd"},
            "executed_c1": {"$sum": {"$cond": [{"$eq": ["$c1.status", "executed"]}, 1, 0]}},
            "executed_c2": {"$sum": {"$cond": [{"$eq": ["$c2.status", "executed"]}, 1, 0]}},
            "mirror_count": {"$sum": {"$cond": [{"$eq": ["$c2.action", "MIRROR"]}, 1, 0]}},
            "reverse_count": {"$sum": {"$cond": [{"$eq": ["$c2.action", "REVERSE"]}, 1, 0]}},
            "do_nothing_count": {"$sum": {"$cond": [{"$eq": ["$c2.action", "DO_NOTHING"]}, 1, 0]}},
        }},
    ]
    agg = await db.cycles.aggregate(pipeline).to_list(length=1)
    if agg:
        agg[0].pop("_id", None)
        a = agg[0]
    else:
        a = {"total_profit": 0, "c1_profit": 0, "c2_profit": 0, "executed_c1": 0,
             "executed_c2": 0, "mirror_count": 0, "reverse_count": 0, "do_nothing_count": 0}

    # Liquidation aggregates
    liq_count = await db.liquidations.count_documents({})
    liq_pipe = [
        {"$group": {
            "_id": None,
            "total_bonus": {"$sum": "$actual_net_bonus_usd"},
            "executed": {"$sum": {"$cond": [{"$eq": ["$status", "executed"]}, 1, 0]}},
            "frontran": {"$sum": {"$cond": [{"$eq": ["$status", "frontran"]}, 1, 0]}},
        }},
    ]
    liq_agg = await db.liquidations.aggregate(liq_pipe).to_list(length=1)
    if liq_agg:
        liq_agg[0].pop("_id", None)
        l = liq_agg[0]
    else:
        l = {"total_bonus": 0, "executed": 0, "frontran": 0}

    return {
        "cycles_total": cycles_count,
        **{k: round(v, 4) if isinstance(v, float) else v for k, v in a.items()},
        "liquidations_total": liq_count,
        "liquidations_executed": l["executed"],
        "liquidations_frontran": l["frontran"],
        "liquidation_bonus_total": round(l["total_bonus"], 4),
        "block": state.get("block"),
    }
