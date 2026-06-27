from __future__ import annotations

import pytest

from apex_omega_core.core.v2_cpmm_math import (
    amount_out_cpmm,
    two_pool_cpmm_optimal_input,
    two_pool_cpmm_profit,
)


def _ternary_max_profit(
    upper: float,
    reserve_in_a: float,
    reserve_out_a: float,
    reserve_in_b: float,
    reserve_out_b: float,
    fee_bps_a: float = 30.0,
    fee_bps_b: float = 30.0,
) -> tuple[float, float]:
    lo = 0.0
    hi = upper
    for _ in range(220):
        left = lo + (hi - lo) / 3.0
        right = hi - (hi - lo) / 3.0
        if two_pool_cpmm_profit(
            left,
            reserve_in_a,
            reserve_out_a,
            reserve_in_b,
            reserve_out_b,
            fee_bps_a,
            fee_bps_b,
        ) < two_pool_cpmm_profit(
            right,
            reserve_in_a,
            reserve_out_a,
            reserve_in_b,
            reserve_out_b,
            fee_bps_a,
            fee_bps_b,
        ):
            lo = left
        else:
            hi = right
    x = (lo + hi) / 2.0
    return x, two_pool_cpmm_profit(
        x,
        reserve_in_a,
        reserve_out_a,
        reserve_in_b,
        reserve_out_b,
        fee_bps_a,
        fee_bps_b,
    )


def test_amount_out_cpmm_uses_10000_bps_scale() -> None:
    # 1000 in, 30 bps fee, 1:1 reserves:
    # floor((1000 * 9970 * 1_000_000) / (1_000_000 * 10_000 + 1000 * 9970))
    assert amount_out_cpmm(1000, 1_000_000, 1_000_000, fee_bps=30) == 996


def test_two_pool_cpmm_optimum_matches_numerical_profit_peak() -> None:
    reserves = (1_000_000.0, 2_000_000.0, 1_000_000.0, 900_000.0)
    analytical = two_pool_cpmm_optimal_input(*reserves, 30.0, 30.0)
    numerical, numerical_profit = _ternary_max_profit(analytical * 2.5, *reserves)
    analytical_profit = two_pool_cpmm_profit(analytical, *reserves, 30.0, 30.0)

    assert analytical == pytest.approx(numerical, rel=1e-7)
    assert analytical_profit == pytest.approx(numerical_profit, rel=1e-9)
    assert analytical_profit > 0.0


def test_two_pool_cpmm_optimum_supports_asymmetric_fees() -> None:
    reserves = (2_500_000.0, 4_750_000.0, 2_100_000.0, 1_650_000.0)
    analytical = two_pool_cpmm_optimal_input(*reserves, 5.0, 30.0)
    numerical, numerical_profit = _ternary_max_profit(analytical * 2.5, *reserves, 5.0, 30.0)
    analytical_profit = two_pool_cpmm_profit(analytical, *reserves, 5.0, 30.0)

    assert analytical == pytest.approx(numerical, rel=1e-7)
    assert analytical_profit == pytest.approx(numerical_profit, rel=1e-9)
    assert analytical_profit > 0.0


def test_two_pool_cpmm_optimum_returns_zero_without_structural_edge() -> None:
    assert two_pool_cpmm_optimal_input(
        1_000_000.0,
        1_000_000.0,
        1_000_000.0,
        900_000.0,
        30.0,
        30.0,
    ) == 0.0
