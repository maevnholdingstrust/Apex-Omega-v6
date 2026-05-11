from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .zone import classify_flash_ladder_zone

DEFAULT_STEP1_TVL_FRACTION = 0.15


@dataclass(frozen=True)
class SizeLadderPoint:
    amount_usd: float
    fraction_of_step1_tvl: float
    zone: str


def classify_size_zone(fraction_of_step1_tvl: float) -> str:
    return classify_flash_ladder_zone(fraction_of_step1_tvl)


def build_size_ladder(
    *,
    step1_pool_tvl_usd: float,
    min_flash_loan_usd: float = 50.0,
    max_flash_loan_usd: float = 1_000_000.0,
    reserve_based_optimal_input_usd: Optional[float] = None,
    max_slippage_size_usd: Optional[float] = None,
    downstream_route_depth_limit_usd: Optional[float] = None,
    available_flashloan_liquidity_usd: Optional[float] = None,
    system_risk_cap_usd: Optional[float] = None,
    max_step1_tvl_fraction: float = DEFAULT_STEP1_TVL_FRACTION,
    scan_fractions: Optional[Iterable[float]] = None,
) -> List[SizeLadderPoint]:
    if step1_pool_tvl_usd <= 0:
        return []

    step1_cap = step1_pool_tvl_usd * max_step1_tvl_fraction

    caps = [step1_cap, max_flash_loan_usd]

    for cap in (
        reserve_based_optimal_input_usd,
        max_slippage_size_usd,
        downstream_route_depth_limit_usd,
        available_flashloan_liquidity_usd,
        system_risk_cap_usd,
    ):
        if cap is not None and cap > 0:
            caps.append(float(cap))

    hard_cap = min(caps)

    if hard_cap < min_flash_loan_usd:
        return []

    fractions = list(scan_fractions or [
        0.001,
        0.0025,
        0.005,
        0.01,
        0.02,
        0.03,
        0.05,
        0.10,
        0.15,
    ])

    sizes = {
        step1_pool_tvl_usd * f
        for f in fractions
        if f > 0 and min_flash_loan_usd <= step1_pool_tvl_usd * f <= hard_cap
    }

    sizes.add(min_flash_loan_usd)
    sizes.add(hard_cap)

    return [
        SizeLadderPoint(
            amount_usd=float(amount),
            fraction_of_step1_tvl=float(amount / step1_pool_tvl_usd),
            zone=classify_size_zone(amount / step1_pool_tvl_usd),
        )
        for amount in sorted(sizes)
    ]
