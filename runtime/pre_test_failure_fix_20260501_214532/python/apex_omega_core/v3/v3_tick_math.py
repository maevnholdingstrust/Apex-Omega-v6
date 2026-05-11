from decimal import Decimal, getcontext

getcontext().prec = 80

MIN_TICK = -887272
MAX_TICK = 887272


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
