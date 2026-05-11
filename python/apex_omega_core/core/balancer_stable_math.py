
from __future__ import annotations

class BalancerStableNotImplemented(NotImplementedError):
    pass

def quote_balancer_stable(*args, **kwargs):
    raise BalancerStableNotImplemented("Balancer stable invariant quote not implemented yet")
