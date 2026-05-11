"""Compatibility shim for the consolidated swap adapter module."""

from .swap_adapters import (
    AlgebraSwapAdapter,
    BalancerSwapAdapter,
    CurveSwapAdapter,
    SwapRequest,
    UniV3SwapAdapter,
    UniversalSwapAdapter,
    V2SwapAdapter,
    encode_route_steps,
)

__all__ = [
    "AlgebraSwapAdapter",
    "BalancerSwapAdapter",
    "CurveSwapAdapter",
    "SwapRequest",
    "UniV3SwapAdapter",
    "UniversalSwapAdapter",
    "V2SwapAdapter",
    "encode_route_steps",
]
