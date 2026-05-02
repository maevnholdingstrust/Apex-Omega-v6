from typing import Any
from .v3_pool_state import V3PoolState, validate_v3_state


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_v3_candidate(candidate: Any) -> bool:
    state = _get(candidate, "v3_state", None)

    if not isinstance(state, V3PoolState):
        return False

    if not validate_v3_state(state):
        return False

    if not _get(candidate, "route_calldata", None):
        return False

    if not bool(_get(candidate, "fork_sim_passed", False)):
        return False

    return True
