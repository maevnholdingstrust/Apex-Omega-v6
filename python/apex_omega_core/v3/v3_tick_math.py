from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext

getcontext().prec = 80

MIN_TICK = -887272
MAX_TICK = 887272
Q96 = 2 ** 96


@dataclass(frozen=True)
class TickLiquidityNet:
    tick: int
    liquidity_net: int


def tick_to_price(tick: int) -> Decimal:
    if tick < MIN_TICK or tick > MAX_TICK:
        raise ValueError("tick out of Uniswap V3 bounds")
    return Decimal("1.0001") ** Decimal(tick)


def validate_tick_spacing(tick: int, tick_spacing: int) -> bool:
    if tick_spacing <= 0:
        return False
    return tick % tick_spacing == 0


def is_tick_in_bounds(tick: int) -> bool:
    return MIN_TICK <= tick <= MAX_TICK


def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    raw = (int(sqrt_price_x96) / Q96) ** 2
    return raw * (10 ** (int(decimals0) - int(decimals1)))
