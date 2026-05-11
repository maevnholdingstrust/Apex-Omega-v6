
from __future__ import annotations

class CurveStableSwapNotImplemented(NotImplementedError):
    pass

def quote_curve_stableswap(*args, **kwargs):
    raise CurveStableSwapNotImplemented("Curve StableSwap invariant quote not implemented yet")
