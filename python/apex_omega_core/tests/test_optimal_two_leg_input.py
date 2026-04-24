"""Tests for the closed-form two-pool CPMM optimal-input formula.

Verifies:
  1. No-arb pools return 0 (when prices are equal).
  2. The closed-form maximises ``two_leg_arb_profit['p_gross']`` better
     than naive grid sizes (positive profit at the optimum).
  3. The formula correctly rejects fee-eating spreads (returns 0).
"""

from __future__ import annotations

from apex_omega_core.core.slippage_sentinel import SlippageSentinel


def test_no_arb_returns_zero() -> None:
    s = SlippageSentinel()
    # Two identical pools — no arbitrage possible
    x = s.optimal_two_leg_input(
        r1_in=1_000_000, r1_out=1_000_000, fee1=0.003,
        r2_in=1_000_000, r2_out=1_000_000, fee2=0.003,
    )
    assert x == 0.0


def test_optimum_is_actually_profitable() -> None:
    """A real arb cycle: pool 1 sells B cheaper than pool 2 buys it."""
    s = SlippageSentinel()
    # Pool 1: 1M A vs 1.02M B (B is "cheap" here, ~2% premium)
    # Pool 2: 1M B vs 1M A (B and A 1:1)
    r1_in, r1_out, f1 = 1_000_000.0, 1_020_000.0, 0.0005
    r2_in, r2_out, f2 = 1_000_000.0, 1_000_000.0, 0.0005

    x_star = s.optimal_two_leg_input(r1_in, r1_out, f1, r2_in, r2_out, f2)
    assert x_star > 0.0, "expected a positive optimal size for a real arb"

    profit_at_opt = s.two_leg_arb_profit(
        x_star, f1, r1_in, r1_out, f2, r2_in, r2_out,
    )["p_gross"]
    assert profit_at_opt > 0.0, "optimum must produce strictly positive gross profit"

    # The closed-form should beat both halving and doubling the size
    half = s.two_leg_arb_profit(
        x_star * 0.5, f1, r1_in, r1_out, f2, r2_in, r2_out,
    )["p_gross"]
    dbl = s.two_leg_arb_profit(
        x_star * 2.0, f1, r1_in, r1_out, f2, r2_in, r2_out,
    )["p_gross"]
    assert profit_at_opt >= half
    assert profit_at_opt >= dbl


def test_fees_can_kill_a_marginal_arb() -> None:
    """Tiny price discrepancy with high fees → no profitable size."""
    s = SlippageSentinel()
    # 0.01% mid-price gap, 1% fees on each side → no-arb
    x = s.optimal_two_leg_input(
        r1_in=1_000_000, r1_out=1_000_100, fee1=0.01,
        r2_in=1_000_000, r2_out=1_000_000, fee2=0.01,
    )
    assert x == 0.0
