from typing import Any
from apex_omega_core.v3.uniswap_v3_router import build_uniswap_v3_route
from apex_omega_core.v3.algebra_router import build_algebra_route


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_v2_route(candidate: Any) -> dict:
    pool_type = str(_get(candidate, "pool_type", _get(candidate, "type", ""))).upper()
    if pool_type in {"V3", "UNISWAP_V3", "ALGEBRA"}:
        raise ValueError("V2 route builder cannot build V3/Algebra candidates")
    return {
        "pool_type": "V2",
        "route": _get(candidate, "route", None),
        "calldata": _get(candidate, "route_calldata", None),
    }


def build_route(candidate: Any) -> dict:
    pool_type = str(_get(candidate, "pool_type", _get(candidate, "type", ""))).upper()

    if pool_type in {"V3", "UNISWAP_V3"}:
        return build_uniswap_v3_route(candidate)

    if pool_type == "ALGEBRA":
        return build_algebra_route(candidate)

    return build_v2_route(candidate)
