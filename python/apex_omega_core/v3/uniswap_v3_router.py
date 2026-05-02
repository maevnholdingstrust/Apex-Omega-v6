from typing import Any


def build_uniswap_v3_route(candidate: Any) -> dict:
    """
    Mechanical V3 route object.
    Actual calldata must be produced by the router codec or external ABI builder.
    """
    if not getattr(candidate, "v3_tick_validated", False) and not (
        isinstance(candidate, dict) and candidate.get("v3_tick_validated")
    ):
        raise ValueError("V3 route cannot be built without tick validation")

    return {
        "pool_type": "UNISWAP_V3",
        "route": getattr(candidate, "route", None) if not isinstance(candidate, dict) else candidate.get("route"),
        "calldata": getattr(candidate, "route_calldata", None) if not isinstance(candidate, dict) else candidate.get("route_calldata"),
    }
