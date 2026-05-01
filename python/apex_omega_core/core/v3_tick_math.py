
from __future__ import annotations

Q96 = 2 ** 96

class V3TickMathNotImplemented(NotImplementedError):
    pass

def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    raw = (sqrt_price_x96 / Q96) ** 2
    return raw * (10 ** (decimals0 - decimals1))

def quote_v3_exact_input(*args, **kwargs):
    raise V3TickMathNotImplemented("V3 tick traversal quote not implemented yet")
