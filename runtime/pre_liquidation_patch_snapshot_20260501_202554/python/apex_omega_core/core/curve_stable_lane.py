
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class CurvePoolState:
    pool: str
    tokens: list[str]
    balances: list[int]
    decimals: list[int]
    amp: int
    fee_bps: int

def validate_curve_state(state: CurvePoolState) -> tuple[bool, str]:
    if len(state.tokens) < 2:
        return False, "not_enough_tokens"
    if len(state.balances) != len(state.tokens):
        return False, "balance_token_length_mismatch"
    if state.amp <= 0:
        return False, "missing_amp"
    if state.fee_bps < 0:
        return False, "invalid_fee"
    return True, "ok"

def quote_curve_guarded(*, state: CurvePoolState, i: int, j: int, dx: int) -> int:
    ok, reason = validate_curve_state(state)
    if not ok:
        raise ValueError(f"Curve quote rejected: {reason}")
    # Full StableSwap invariant must be implemented before execution.
    raise NotImplementedError("Curve StableSwap invariant quote not implemented yet")
