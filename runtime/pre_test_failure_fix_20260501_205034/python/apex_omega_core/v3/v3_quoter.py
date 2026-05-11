from dataclasses import dataclass
from typing import Optional
from .v3_pool_state import V3PoolState, validate_v3_state
from .v3_swap_math import quote_v3_spot_exact_in


@dataclass
class V3Quote:
    success: bool
    amount_out: float
    source: str
    reason: Optional[str] = None


def quote_v3_exact_in(state: V3PoolState, amount_in: float, zero_for_one: bool, external_quoter=None) -> V3Quote:
    if not validate_v3_state(state):
        return V3Quote(False, 0.0, "validation", "INVALID_V3_STATE")

    if external_quoter:
        q = external_quoter(state, amount_in, zero_for_one)
        return V3Quote(True, float(q), "external_quoter", None)

    out = quote_v3_spot_exact_in(state, amount_in, zero_for_one)
    return V3Quote(True, out, "spot_math_requires_fork_confirmation", None)
