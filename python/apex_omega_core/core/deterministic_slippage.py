"""Deterministic constant-product slippage calculator.

Provides :func:`calculate_deterministic_slippage_bps`, a closed-form AMM
price-impact function that replaces heuristic or ML-predicted slippage
estimates with real constant-product math.

Supported AMM types:

``"v2"``
    Uniswap V2 / SushiSwap / QuickSwap V2 — standard constant-product
    (x * y = k).

``"v3"``
    Uniswap V3 / QuickSwap V3 — concentrated liquidity; the active-range
    depth is amplified by ``v3_concentration`` before computing impact.

``"aerodrome"``
    Aerodrome / Solidly vAMM volatile pairs — the curve x^3*y + y^3*x = k
    is slightly shallower than pure CPMM for large trades; modelled with a
    5 % effective-reserve discount.

No artificial ceiling is applied.  The value returned grows towards
10 000 bps as ``trade_size`` approaches ``pool_tvl``, reflecting true
constant-product math.
"""

from __future__ import annotations

__all__ = ["calculate_deterministic_slippage_bps", "calculate_real_profit"]


def calculate_deterministic_slippage_bps(
    trade_size: float,
    pool_tvl: float,
    dex: str = "v2",
    v3_concentration: float = 1.0,
    fee_bps: float = 30.0,
) -> float:
    """Calculate AMM price impact in basis points using constant-product math.

    The function approximates the single-side reserve as ``pool_tvl / 2``
    (balanced-pool assumption) and applies the exact CPMM impact formula after
    accounting for the pool fee.  For V3 the effective reserve is amplified by
    the tick-range concentration factor; for Aerodrome vAMM a slight reserve
    discount models the steeper Solidly curve.

    Parameters
    ----------
    trade_size:
        Notional trade size in the same unit as ``pool_tvl`` (USD recommended).
    pool_tvl:
        Total pool liquidity (both sides combined) in the same unit as
        ``trade_size``.  For a two-sided pool this is
        ``reserve_in_usd + reserve_out_usd``.
    dex:
        AMM type — ``"v2"``, ``"v3"``, or ``"aerodrome"``.
        Unknown values fall back to V2 behaviour.
    v3_concentration:
        Liquidity amplification factor for V3 concentrated-liquidity pools.
        A tighter price range concentrates reserves into the active tick,
        effectively deepening the pool and reducing per-dollar slippage.
        Typical values: 1.5–20×.  Defaults to 1.0 (no amplification).
        Ignored for non-V3 AMM types.
    fee_bps:
        Pool fee in basis points (e.g. ``30`` for 0.30 %).
        Defaults to 30 bps (standard V2 fee).

    Returns
    -------
    float
        Price impact in basis points.  Returns ``0.0`` for degenerate or
        zero inputs.  No ceiling is applied — values above 5 000 bps are
        possible for trades that approach pool size.

    Examples
    --------
    >>> # $10k trade on a $200k V2 pool (30 bps fee)
    >>> calculate_deterministic_slippage_bps(10_000, 200_000, dex="v2", fee_bps=30)
    933.9...   # ≈ 934 bps (trade is ~10% of one-side reserve after fee)

    >>> # Same trade on a $2M V3 pool with 5× concentration
    >>> calculate_deterministic_slippage_bps(10_000, 2_000_000, dex="v3", v3_concentration=5.0, fee_bps=5)
    19.9...    # ≈ 20 bps
    """
    if trade_size <= 0.0 or pool_tvl <= 0.0:
        return 0.0

    # Single-side reserve: balanced-pool approximation.
    # For a two-sided pool (reserve_in + reserve_out = pool_tvl) at mid-price
    # equilibrium, each side holds half the total value.
    reserve = pool_tvl / 2.0

    dex_lower = dex.lower()
    if dex_lower == "v3":
        # Concentrated liquidity amplifies the effective depth within the
        # active tick range.  A concentration factor > 1 means the range is
        # tighter than the full price curve, so the same TVL provides more
        # depth per unit of price movement.
        effective_reserve = reserve * max(1.0, float(v3_concentration))
    elif dex_lower == "aerodrome":
        # Aerodrome vAMM volatile pairs use x^3*y + y^3*x = k, which is
        # slightly shallower than pure CPMM for large trades.  A 5 % reserve
        # discount conservatively captures this without implementing the full
        # Solidly invariant.
        effective_reserve = reserve * 0.95
    else:
        # V2 / default: standard constant-product x*y = k.
        effective_reserve = reserve

    # Fee-adjusted CPMM impact formula (exact, no Taylor approximation):
    #
    #   trade_after_fee = trade_size * fee_factor          (fee subtracted at entry)
    #   actual_out      = trade_after_fee * R / (R + trade_after_fee)
    #   expected_out    = trade_size                       (mid-price, balanced pool)
    #   impact          = (expected_out − actual_out) / expected_out
    #
    # Expanding:
    #   impact = 1 − fee_factor * R / (R + fee_factor * trade_size)
    #          = (R*(1−fee_factor) + fee_factor*trade_size) / (R + fee_factor*trade_size)
    fee_factor = max(0.0, 1.0 - float(fee_bps) / 10_000.0)
    trade_after_fee = trade_size * fee_factor
    denom = effective_reserve + trade_after_fee
    if denom <= 0.0:
        return 0.0

    actual_out = trade_after_fee * effective_reserve / denom
    # expected_out = trade_size (balanced pool: reserve_out / reserve_in = 1)
    impact = max(0.0, (trade_size - actual_out) / trade_size)

    return impact * 10_000.0


def calculate_real_profit(
    trade_size: float,
    price_diff_bps: float,
    pool_tvl: float,
    dex: str = "v2",
    gas_cost: float = 12.0,
    fee_bps: float = 30.0,
    v3_concentration: float = 1.0,
    protocol_fee_rate: float = 0.0008,
    min_profit_threshold: float = 8.0,
) -> dict:
    """Return realistic net profit after slippage, fees, and gas.

    Composites :func:`calculate_deterministic_slippage_bps` with gross-profit
    and cost accounting into a single P&L breakdown dict.  This is the drop-in
    function to call wherever the codebase needs a quick profitability estimate
    without a full :class:`~.slippage_sentinel.SlippageSentinel` pipeline run.

    Parameters
    ----------
    trade_size:
        Notional trade / flash-loan size in USD.
    price_diff_bps:
        Observed price difference between buy and sell venue in basis points
        (e.g. ``850`` for an 8.5 % spread).
    pool_tvl:
        Total USD liquidity of the *smallest* (constraining) pool in the route.
        Pass the minimum TVL across all legs to get the worst-case slippage.
    dex:
        AMM type — ``"v2"``, ``"v3"``, or ``"aerodrome"``.  Defaults to ``"v2"``.
    gas_cost:
        Estimated gas cost in USD for the full transaction (flash-loan +
        two swaps + repayment).  Defaults to ``$12.00`` (Polygon mainnet
        typical for a two-swap MEV bundle at ~150 gwei).
    fee_bps:
        Pool fee in basis points passed through to the slippage calculator.
        Defaults to ``30`` (0.30 % — standard V2 fee).
    v3_concentration:
        V3 liquidity amplification factor.  Ignored for non-V3 pools.
        Defaults to ``1.0``.
    protocol_fee_rate:
        Fractional protocol / flash-loan fee applied to ``trade_size``
        (e.g. ``0.0008`` = 0.08 % — Balancer flash-loan fee).
        Defaults to ``0.0008``.
    min_profit_threshold:
        Minimum USD net profit required to classify the trade as profitable.
        Defaults to ``$8.00`` (owner-profit floor used throughout the pipeline).

    Returns
    -------
    dict
        Keys:

        * ``gross_profit``  – ``trade_size × price_diff_bps / 10 000`` (USD)
        * ``slippage_loss`` – ``trade_size × slippage_bps / 10 000`` (USD)
        * ``fees``          – protocol / flash-loan fee in USD
        * ``gas``           – gas cost in USD (echo of the ``gas_cost`` parameter)
        * ``net_profit``    – ``gross_profit − slippage_loss − fees − gas`` (USD)
        * ``slippage_bps``  – raw CPMM price impact in basis points (float)
        * ``is_profitable`` – ``True`` when ``net_profit > min_profit_threshold``

    Examples
    --------
    >>> calculate_real_profit(1292.03, 1850, 12920.30, dex="v2")
    {'gross_profit': 239.03, 'slippage_loss': ..., ..., 'is_profitable': False}
    """
    slippage_bps = calculate_deterministic_slippage_bps(
        trade_size=trade_size,
        pool_tvl=pool_tvl,
        dex=dex,
        v3_concentration=v3_concentration,
        fee_bps=fee_bps,
    )

    gross_profit = trade_size * (price_diff_bps / 10_000.0)
    slippage_loss = trade_size * (slippage_bps / 10_000.0)
    fees = trade_size * protocol_fee_rate
    net_profit = gross_profit - slippage_loss - fees - gas_cost

    return {
        "gross_profit": round(gross_profit, 2),
        "slippage_loss": round(slippage_loss, 2),
        "fees": round(fees, 2),
        "gas": gas_cost,
        "net_profit": round(net_profit, 2),
        "slippage_bps": round(slippage_bps, 0),
        "is_profitable": net_profit > min_profit_threshold,
    }
