from typing import Any


def build_algebra_route(candidate: Any) -> dict:
    if not getattr(candidate, "v3_tick_validated", False) and not (
        isinstance(candidate, dict) and candidate.get("v3_tick_validated")
    ):
        raise ValueError("Algebra route cannot be built without tick validation")

    return {
        "pool_type": "ALGEBRA",
        "route": getattr(candidate, "route", None) if not isinstance(candidate, dict) else candidate.get("route"),
        "calldata": getattr(candidate, "route_calldata", None) if not isinstance(candidate, dict) else candidate.get("route_calldata"),
    }
