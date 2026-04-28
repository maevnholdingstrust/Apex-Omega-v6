"""Uniswap V3 virtual-reserve math.

At the active tick a V3 concentrated-liquidity position behaves like a
constant-product (CPMM) pool whose *virtual* reserves are derived from
``sqrtPriceX96`` and the active ``liquidity``.  Using these virtual
reserves inside the standard CPMM swap formula (``xy = k``) gives an
accurate approximation of the executed price for trades that do not
move the price across tick boundaries — which holds for the small,
relative-to-TVL arb sizes targeted by this system.

Reference
---------
Uniswap V3 whitepaper, §6.2:

    x = L / sqrt_p
    y = L * sqrt_p

where ``sqrt_p = sqrtPriceX96 / 2**96`` and ``L`` is the active
liquidity.  ``x`` and ``y`` are in raw token base units; dividing by
``10**dec0`` and ``10**dec1`` converts them to decimal-normalised units.

Exports
-------
v3_virtual_reserves
    Given ``sqrtPriceX96``, ``liquidity``, and token decimals, return
    ``(reserve0, reserve1)`` in decimal-normalised token units.

v3_spot_price
    Decimal-normalised spot price (token1 per token0) from
    ``sqrtPriceX96``.
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def v3_virtual_reserves(
    sqrt_price_x96: float,
    liquidity: float,
    dec0: int = 18,
    dec1: int = 18,
) -> tuple[float, float]:
    """Return ``(reserve0, reserve1)`` virtual reserves for a V3 pool.

    The virtual reserves are in decimal-normalised token units (e.g.
    ``1.0`` = 1 WETH at 18 decimals, ``1.0`` = 1 USDC at 6 decimals).
    They are suitable as the ``reserve_in`` / ``reserve_out`` inputs to
    the standard CPMM swap formula.

    Parameters
    ----------
    sqrt_price_x96 :
        ``sqrtPriceX96`` from the pool's ``slot0()`` call.  Equal to
        ``sqrt(token1_raw / token0_raw) * 2**96``.
    liquidity :
        Active virtual liquidity from ``liquidity()`` (raw ``uint128``).
    dec0 : int
        Decimal places for ``token0`` (default 18).
    dec1 : int
        Decimal places for ``token1`` (default 18).

    Returns
    -------
    (reserve0, reserve1) : tuple[float, float]
        Decimal-normalised virtual reserves.  Returns ``(0.0, 0.0)``
        when either input is non-positive or non-finite.
    """
    if not (sqrt_price_x96 > 0 and liquidity > 0):
        return (0.0, 0.0)
    if not (math.isfinite(sqrt_price_x96) and math.isfinite(liquidity)):
        return (0.0, 0.0)

    sqrt_p = sqrt_price_x96 / (2 ** 96)
    if sqrt_p <= 0:
        return (0.0, 0.0)

    # Raw virtual reserves in token base units
    vr0_raw = liquidity / sqrt_p   # token0 side
    vr1_raw = liquidity * sqrt_p   # token1 side

    # Decimal-normalised
    reserve0 = vr0_raw / (10 ** dec0)
    reserve1 = vr1_raw / (10 ** dec1)

    if not (math.isfinite(reserve0) and math.isfinite(reserve1)):
        return (0.0, 0.0)

    return (reserve0, reserve1)


def v3_spot_price(
    sqrt_price_x96: float,
    dec0: int = 18,
    dec1: int = 18,
) -> float:
    """Return the decimal-normalised spot price (token1 per token0).

    Parameters
    ----------
    sqrt_price_x96 :
        ``sqrtPriceX96`` from slot0.
    dec0, dec1 :
        Decimal places for token0 and token1.

    Returns
    -------
    float
        Spot price in token1/token0 decimal units.  Returns ``0.0`` for
        invalid inputs.
    """
    if not (sqrt_price_x96 > 0 and math.isfinite(sqrt_price_x96)):
        return 0.0

    sqrt_p = sqrt_price_x96 / (2 ** 96)
    # price_raw = token1_raw / token0_raw = sqrt_p^2
    # price_decimal = price_raw * 10^dec0 / 10^dec1
    price = (sqrt_p ** 2) * (10 ** dec0) / (10 ** dec1)

    return price if math.isfinite(price) else 0.0
