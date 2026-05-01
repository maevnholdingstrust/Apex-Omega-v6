"""Deterministic, constant-product (CPMM) slippage estimation.

This module replaces any heuristic or ML-based slippage predictors with
exact AMM math.  No hard clamps; outputs are the real average-execution
price impact implied by the xy = k invariant.

Exports
-------
calculate_deterministic_slippage_bps
    Main entry point.  Given a trade size and pool TVL, returns price
    impact in basis points using CPMM math adjusted for V2, V3, or
    Aerodrome pool geometry.

calculate_cpmm_output_slippage_bps
    Compare the actual AMM output against the no-impact (marginal price)
    output to express slippage as basis points of the principal.

max_leg_slippage_bps
    Convenience wrapper that computes per-leg slippage and returns the
    worst-case leg for a multi-hop route.
"""

from __future__ import annotations

from typing import List, Dict, Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cpmm_avg_impact(trade_size: float, reserve: float) -> float:
    """Average execution price impact for a balanced CPMM pool.

    Derivation
    ----------
    For a pool with reserve R and trade x the CPMM output is:

        out = R_out * x / (R_in + x)          (ignoring fees for impact only)

    The no-impact (marginal price) output is:

        out_ideal = (R_out / R_in) * x  =  x  (normalised, balanced pool)

    The average execution price is:

        avg_price = out / x = R_out / (R_in + x) = R / (R + x)

    So the average impact (fraction of principal) is:

        impact = 1 - R / (R + x)

    This is the accurate CPMM measure.  For a $10k trade into a $1M TVL
    pool (R = $500k per side) this gives:

        impact = 1 - 500_000 / (500_000 + 10_000) ≈ 0.0196 = 196 bps

    which is materially different from the S/(R-S) approximation that
    over-states impact for larger trades.

    Parameters
    ----------
    trade_size : dollar-denominated size of the trade leg.
    reserve    : dollar-denominated reserve of the input-side token
                 (= pool_tvl / 2 for a balanced 50/50 pool).

    Returns
    -------
    float in [0, 1) representing the fractional average price impact.
    """
    if reserve <= 0.0 or trade_size <= 0.0:
        return 1.0  # degenerate pool or no trade — full impact
    return 1.0 - reserve / (reserve + trade_size)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_deterministic_slippage_bps(
    trade_size: float,
    pool_tvl: float,
    dex: str = "v2",
    v3_concentration: float = 1.0,
    fee_bps: float = 30.0,
) -> float:
    """Return deterministic slippage in basis points using CPMM math.

    This is the single source of truth for pre-execution slippage
    estimation.  It does NOT clamp output.

    Parameters
    ----------
    trade_size :
        Dollar-denominated size of the trade on the input leg.
    pool_tvl :
        Total value locked in the pool (both sides combined, USD).
    dex :
        Pool geometry.  One of ``"v2"`` (classic 50/50 CPMM),
        ``"v3"`` (Uniswap V3 concentrated liquidity), or
        ``"aerodrome"`` (Aerodrome/Velodrome sAMM/vAMM, treated as V2
        with their characteristic fee structure).
    v3_concentration :
        For ``dex="v3"`` only.  The ratio of the active virtual reserve
        to the full-range TVL.  Values > 1 mean liquidity is concentrated
        near the current price (tighter range → smaller effective
        reserve → more impact).  Default ``1.0`` is equivalent to a
        full-range V2 pool.
    fee_bps :
        Swap fee in basis points (e.g. 30 for 0.30%, 5 for 0.05%).
        The fee is applied to the effective principal *before* the
        impact calculation so the reserve size used for slippage is the
        post-fee trade size.

    Returns
    -------
    float
        Slippage in basis points (≥ 0).  For example, 166.6 means the
        average execution price is 1.666% worse than the marginal price.
    """
    if trade_size <= 0.0 or pool_tvl <= 0.0:
        return 0.0

    fee_decimal = fee_bps / 10_000.0
    # Effective trade size after the DEX fee is taken
    effective_size = trade_size * (1.0 - fee_decimal)

    if dex == "v3":
        # V3 concentrated liquidity: active virtual reserve is smaller than
        # full-range TVL would imply.  Higher concentration → smaller
        # effective reserve → larger price impact.
        # active_reserve ≈ (pool_tvl / 2) / v3_concentration
        concentration = max(v3_concentration, 1.0)
        reserve = (pool_tvl / 2.0) / concentration
    elif dex in ("aerodrome", "velodrome"):
        # Aerodrome vAMM is a standard xy=k pool; sAMM (stable) has much
        # tighter curves, but this module is used only for vAMM pools.
        # Treat exactly as V2.
        reserve = pool_tvl / 2.0
    else:
        # Default: V2 / QuickSwap / Sushiswap / Balancer 50/50
        reserve = pool_tvl / 2.0

    impact = _cpmm_avg_impact(effective_size, reserve)
    return impact * 10_000.0  # convert to basis points


def calculate_cpmm_output_slippage_bps(
    actual_out: float,
    no_impact_out: float,
) -> float:
    """Express slippage as basis points of the no-impact (ideal) output.

    This is useful after a simulation when you have the exact AMM output
    and want to compare it to the marginal-price output.

    Parameters
    ----------
    actual_out     : Tokens received from the AMM swap.
    no_impact_out  : Tokens that would have been received at the marginal
                     price (i.e. with zero price impact).

    Returns
    -------
    float
        Slippage in basis points (≥ 0).  Returns 0 if ``no_impact_out``
        is zero or negative to avoid division by zero.
    """
    if no_impact_out <= 0.0:
        return 0.0
    shortfall = no_impact_out - actual_out
    return max(0.0, shortfall / no_impact_out * 10_000.0)


def calculate_real_profit(
    final_amount_out: float,
    amount_in: float,
    gas_cost_usd: float = 0.0,
    flash_fee_usd: float = 0.0,
    risk_buffer_usd: float = 0.0,
    extra_costs_usd: float = 0.0,
) -> float:
    """Real executable profit after all known costs.

    Assumption
    ----------
    ``final_amount_out`` and ``amount_in`` are already expressed in the same
    USD basis or the same profit-token value basis.

    Parameters
    ----------
    final_amount_out : Token-out value after both swaps (USD or token units).
    amount_in        : Flash-loaned / initial capital (same denomination).
    gas_cost_usd     : Estimated on-chain gas cost in USD.
    flash_fee_usd    : Flash-loan origination fee in USD.
    risk_buffer_usd  : Discretionary risk/slippage buffer in USD.
    extra_costs_usd  : Any other protocol or bridge fees in USD.

    Returns
    -------
    float
        Net profit.  Negative means the trade would be a loss.
    """
    gross_profit = final_amount_out - amount_in
    total_costs = flash_fee_usd + risk_buffer_usd + extra_costs_usd
    return gross_profit - total_costs


def max_leg_slippage_bps(
    legs: List[Dict[str, Any]],
    trade_size_usd: float,
    dex_key: str = "venue",
    tvl_key: str = "pool_tvl_usd",
    fee_key: str = "fee",
) -> float:
    """Return the worst-case slippage (in bps) across all legs of a route.

    Each element of *legs* must be a dict with at minimum:
    - ``dex_key``  – pool venue string ("v2", "v3", "aerodrome", etc.)
    - ``tvl_key``  – USD TVL of the pool
    - ``fee_key``  – fee as a decimal (e.g. 0.003 for 0.3%)

    Parameters
    ----------
    legs           : List of leg dicts describing each hop of the route.
    trade_size_usd : USD size of the trade (same for all legs as the
                     principal propagates through consecutive hops at
                     roughly the same USD value).
    dex_key, tvl_key, fee_key : dict key names for venue, TVL, and fee.

    Returns
    -------
    float
        Maximum per-leg deterministic slippage in basis points.
    """
    if not legs:
        return 0.0

    worst = 0.0
    for leg in legs:
        venue = str(leg.get(dex_key, "v2")).lower()
        pool_tvl = float(leg.get(tvl_key, 0.0))
        fee_decimal = float(leg.get(fee_key, 0.003))
        fee_bps_val = fee_decimal * 10_000.0 if fee_decimal <= 1.0 else fee_decimal

        # Map venue string to dex category
        if "v3" in venue or "univ3" in venue or "uniswap" in venue:
            dex_cat = "v3"
        elif "aerodrome" in venue or "velodrome" in venue:
            dex_cat = "aerodrome"
        else:
            dex_cat = "v2"

        slip = calculate_deterministic_slippage_bps(
            trade_size=trade_size_usd,
            pool_tvl=pool_tvl,
            dex=dex_cat,
            fee_bps=fee_bps_val,
        )
        if slip > worst:
            worst = slip

    return worst
