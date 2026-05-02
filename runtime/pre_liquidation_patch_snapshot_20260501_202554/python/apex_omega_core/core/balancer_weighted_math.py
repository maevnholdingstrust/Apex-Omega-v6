
from __future__ import annotations

class BalancerWeightedNotImplemented(NotImplementedError):
    pass

def quote_balancer_weighted(*args, **kwargs):
    raise BalancerWeightedNotImplemented("Balancer weighted invariant quote not implemented yet")
