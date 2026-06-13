"""Top-N opportunity ranker.

The only ranking criterion is net_profit_usd after:
  - flashloan borrow + repay fee
  - all AMM swap fees (both legs)
  - gas cost

No spread-BPS gate. No minimum profit threshold here.
The caller decides how many to take (default 25).
"""
from __future__ import annotations

from typing import List

from ..orchestrator.cycle_orchestrator import Opportunity


def select_top_n(
    opportunities: List[Opportunity],
    n: int = 25,
) -> List[Opportunity]:
    """Return the top-N opportunities ranked by net_profit_usd descending.

    Parameters
    ----------
    opportunities:
        All candidates from the discovery scan (all venues, all pairs, TVL >= $1,000).
    n:
        Maximum number to return. Defaults to 25.

    Returns
    -------
    list[Opportunity]
        Up to ``n`` opportunities sorted by ``net_profit_usd`` descending.
        May be shorter than ``n`` if fewer opportunities exist.
    """
    return sorted(opportunities, key=lambda o: o.net_profit_usd, reverse=True)[:n]
