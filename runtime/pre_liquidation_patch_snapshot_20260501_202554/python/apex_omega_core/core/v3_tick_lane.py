
from __future__ import annotations

from dataclasses import dataclass

Q96 = 2 ** 96

@dataclass(frozen=True)
class V3PoolState:
    pool: str
    token0: str
    token1: str
    fee_tier: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    tick_spacing: int | None = None
    initialized_ticks_loaded: bool = False

def price_from_sqrt_price_x96(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    return (sqrt_price_x96 / Q96) ** 2 * (10 ** (decimals0 - decimals1))

def validate_v3_state(state: V3PoolState) -> tuple[bool, str]:
    if state.sqrt_price_x96 <= 0:
        return False, "missing_or_zero_sqrt_price_x96"
    if state.liquidity <= 0:
        return False, "missing_or_zero_liquidity"
    if state.fee_tier not in {100, 500, 3000, 10000}:
        return False, "unsupported_fee_tier"
    if not state.initialized_ticks_loaded:
        return False, "initialized_ticks_not_loaded"
    return True, "ok"

def quote_v3_exact_input_guarded(*, state: V3PoolState, amount_in: int) -> int:
    ok, reason = validate_v3_state(state)
    if not ok:
        raise ValueError(f"V3 quote rejected: {reason}")
    # Full initialized-tick traversal must be implemented before execution.
    raise NotImplementedError("V3 initialized-tick traversal quote not implemented yet")
