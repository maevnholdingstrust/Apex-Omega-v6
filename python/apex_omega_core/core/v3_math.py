"""Uniswap V3 AMM math — sqrtPriceX96, virtual reserves, single-tick swap.

Uniswap V3 does not use a flat xy = k invariant across the full price range.
Instead, liquidity is concentrated between tick lower and tick upper, and the
swap math uses the concentrated invariant:

    (x + L/√P_upper) × (y + L×√P_lower) = L²

For arbitrage sizing we only need two primitives:

1.  ``v3_virtual_reserves`` — project the active liquidity into equivalent
    "virtual" x and y reserves that can be fed into the existing V2 CPMM
    optimizer.  This approximation is exact within a single tick range and
    gives a usable first-order estimate across wider ranges.

2.  ``v3_get_amount_out`` — compute the output of a single swap within the
    current tick, using the exact V3 single-step formula.

Both functions accept and return *token-native* units (i.e. scaled by 10**dec).
Use the ``sqrt_price_x96_to_price`` helper to convert a raw ``sqrtPriceX96``
slot0 value to a human-readable price.

References
----------
Uniswap V3 whitepaper §6.2 (virtual reserves):
    https://uniswap.org/whitepaper-v3.pdf
"""

from __future__ import annotations

import math

# Uniswap V3 fixed-point denominator: sqrtPriceX96 values are encoded as
# Q64.96 fixed-point numbers.  Dividing by this constant yields the true
# sqrt(P) where P = price of token0 in token1 units.
_Q96: int = 2**96


def sqrt_price_x96_to_price(
    sqrt_price_x96: float,
    dec0: int,
    dec1: int,
) -> float:
    """Convert a Uniswap V3 ``sqrtPriceX96`` slot0 value to a human-readable price.

    Returns the price of *one unit of token0* expressed in *token1*.

    Parameters
    ----------
    sqrt_price_x96 :
        Raw ``sqrtPriceX96`` from the pool's ``slot0()`` return value.
    dec0, dec1 :
        Decimals of token0 and token1 respectively (e.g. 6 for USDC, 18 for WETH).

    Returns
    -------
    float
        Price of 1 token0 in token1 units, adjusted for decimals.
        Returns 0.0 on invalid input.
    """
    if sqrt_price_x96 <= 0.0:
        return 0.0
    # sqrt(P) in token1/token0 raw units
    sqrt_p = sqrt_price_x96 / _Q96
    # P in raw-unit terms
    p_raw = sqrt_p * sqrt_p
    # Adjust for decimal scaling:  price_human = p_raw × 10^(dec0 − dec1)
    return float(p_raw * (10 ** (dec0 - dec1)))


def v3_virtual_reserves(
    sqrt_price_x96: float,
    liquidity: float,
    dec0: int = 18,
    dec1: int = 18,
) -> tuple[float, float]:
    """Compute virtual reserves from Uniswap V3 slot0 state.

    The Uniswap V3 whitepaper §6.2 shows that the active liquidity chunk
    can be expressed as virtual full-range reserves:

        x_virtual = L / √P        (token0 reserve)
        y_virtual = L × √P        (token1 reserve)

    These are expressed in *raw* token units.  Dividing by ``10**dec`` gives
    human-readable amounts.

    This approximation is exact within a single tick range.  For trades that
    cross multiple ticks it over-states the depth, so it is conservative in
    the sense that it may accept trades the real pool would reject due to
    insufficient depth — callers should apply a safety margin on trade size.

    Parameters
    ----------
    sqrt_price_x96 :
        Raw ``sqrtPriceX96`` from slot0.
    liquidity :
        Active liquidity from the pool (``pool.liquidity()``).
    dec0, dec1 :
        Decimals of token0 and token1.

    Returns
    -------
    (reserve0, reserve1) :
        Virtual reserves in *token-native* units (i.e. NOT scaled by decimals).
        Returns (0.0, 0.0) on invalid input.
    """
    if sqrt_price_x96 <= 0.0 or liquidity <= 0.0:
        return 0.0, 0.0

    sqrt_p = sqrt_price_x96 / _Q96

    # Raw virtual reserves
    reserve0_raw = liquidity / sqrt_p
    reserve1_raw = liquidity * sqrt_p

    return float(reserve0_raw), float(reserve1_raw)


def v3_get_amount_out(
    amount_in: float,
    sqrt_price_x96: float,
    liquidity: float,
    fee: float,
    zero_for_one: bool,
) -> float:
    """Compute the output of a single Uniswap V3 swap within the current tick.

    Uses the exact single-step formula from the V3 whitepaper §6.2.  This
    is only valid while the trade stays within the current tick range.  For
    larger trades that cross ticks the result under-counts available output;
    callers should treat this as a conservative lower bound.

    Parameters
    ----------
    amount_in :
        Token-native input amount (raw units, not human-readable).
    sqrt_price_x96 :
        Current ``sqrtPriceX96`` from slot0.
    liquidity :
        Active liquidity.
    fee :
        Swap fee as a decimal (e.g. 0.003 for 0.30%, 0.0005 for 0.05%).
    zero_for_one :
        ``True``  → swap token0 in, token1 out  (price decreases)
        ``False`` → swap token1 in, token0 out  (price increases)

    Returns
    -------
    float
        Token-native output amount.  Returns 0.0 on invalid input.
    """
    if sqrt_price_x96 <= 0.0 or liquidity <= 0.0 or amount_in <= 0.0:
        return 0.0

    fee = max(0.0, min(fee, 1.0))
    sqrt_p = sqrt_price_x96 / _Q96
    amount_in_with_fee = amount_in * (1.0 - fee)

    if zero_for_one:
        # Swap token0 → token1
        # New sqrt price: √P' = L × √P / (L + Δx × √P)
        denominator = liquidity + amount_in_with_fee * sqrt_p
        if denominator <= 0.0:
            return 0.0
        sqrt_p_new = (liquidity * sqrt_p) / denominator
        # Δy = L × (√P − √P')
        delta_y = liquidity * (sqrt_p - sqrt_p_new)
        return max(0.0, float(delta_y))
    else:
        # Swap token1 → token0
        # New sqrt price: √P' = √P + Δy / L
        sqrt_p_new = sqrt_p + amount_in_with_fee / liquidity
        if sqrt_p_new <= 0.0:
            return 0.0
        # Δx = L × (1/√P' − 1/√P)  =  L × (√P − √P') / (√P × √P')
        if sqrt_p <= 0.0 or sqrt_p_new <= 0.0:
            return 0.0
        delta_x = liquidity * (1.0 / sqrt_p - 1.0 / sqrt_p_new)
        return max(0.0, float(delta_x))
