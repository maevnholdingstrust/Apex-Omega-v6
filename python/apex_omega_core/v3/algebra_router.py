from typing import Any

from .v3_route_validator import is_v3_candidate_validated


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_algebra_route(candidate: Any) -> dict:
    if not is_v3_candidate_validated(candidate, require_fork=False):
        raise ValueError("Algebra route cannot be built without tick-aware validation and calldata")

    return {
        "pool_type": "ALGEBRA",
        "route": _get(candidate, "route", None),
        "calldata": _get(candidate, "route_calldata", None),
    }
