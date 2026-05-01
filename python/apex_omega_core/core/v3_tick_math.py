from __future__ import annotations

from apex_omega_core.v3.v3_tick_math import (
    MAX_TICK,
    MIN_TICK,
    Q96,
    TickLiquidityNet,
    is_tick_in_bounds,
    sqrt_price_x96_to_price,
    tick_to_price,
    validate_tick_spacing,
)


class V3TickMathNotImplemented(NotImplementedError):
    pass


def quote_v3_exact_input(*args, **kwargs):
    raise V3TickMathNotImplemented("V3 tick traversal quote not implemented yet")


__all__ = [
    "MAX_TICK",
    "MIN_TICK",
    "Q96",
    "TickLiquidityNet",
    "V3TickMathNotImplemented",
    "is_tick_in_bounds",
    "quote_v3_exact_input",
    "sqrt_price_x96_to_price",
    "tick_to_price",
    "validate_tick_spacing",
]
