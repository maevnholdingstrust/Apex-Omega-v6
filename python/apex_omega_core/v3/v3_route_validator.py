from dataclasses import dataclass
from typing import Any, Optional

from .v3_pool_state import V3PoolState, validate_v3_state


@dataclass(frozen=True)
class V3ValidationResult:
    accepted: bool
    reason: Optional[str] = None


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_v3_candidate(candidate: Any) -> V3ValidationResult:
    state = _get(candidate, "v3_state", None)

    if not isinstance(state, V3PoolState):
        return V3ValidationResult(False, "MISSING_V3_STATE")

    if not validate_v3_state(state):
        return V3ValidationResult(False, "INVALID_V3_STATE")

    if not bool(_get(candidate, "tick_aware_quote_passed", _get(candidate, "v3_tick_validated", False))):
        return V3ValidationResult(False, "MISSING_TICK_AWARE_QUOTE")

    if not _get(candidate, "route_calldata", None):
        return V3ValidationResult(False, "MISSING_CALLDATA")

    if not bool(_get(candidate, "fork_sim_passed", False)):
        return V3ValidationResult(False, "FORK_SIM_NOT_PASSED")

    return V3ValidationResult(True, None)


def is_v3_candidate_validated(candidate: Any, *, require_fork: bool = False) -> bool:
    result = validate_v3_candidate(candidate)
    if result.accepted:
        return True
    if not require_fork and result.reason == "FORK_SIM_NOT_PASSED":
        return True
    return False


def is_v3_candidate_executable(candidate: Any) -> bool:
    return is_v3_candidate_validated(candidate, require_fork=True)
