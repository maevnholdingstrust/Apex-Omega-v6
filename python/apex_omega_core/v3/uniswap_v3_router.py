from typing import Any

from .v3_route_validator import is_v3_candidate_validated


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_uniswap_v3_route(candidate: Any) -> dict:
    """
    Mechanical V3 route object.
    Actual calldata must be produced by the router codec or external ABI builder.
    """
    if not is_v3_candidate_validated(candidate, require_fork=False):
        raise ValueError("V3 route cannot be built without tick-aware validation and calldata")

    return {
        "pool_type": "UNISWAP_V3",
        "route": _get(candidate, "route", None),
        "calldata": _get(candidate, "route_calldata", None),
    }
