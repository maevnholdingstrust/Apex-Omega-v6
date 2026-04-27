"""Unit tests for apex_omega_core.core.deterministic_slippage.

Validates the three public functions exported by the module and verifiable
through the apex_omega_core.core package __all__:

  - calculate_deterministic_slippage_bps
  - calculate_cpmm_output_slippage_bps
  - max_leg_slippage_bps

These tests also guard against stale re-exports in core/__init__.py that
would cause an ImportError during pytest collection (regression for the
'cannot import name calculate_real_profit' failure).
"""

import pytest
from apex_omega_core.core.deterministic_slippage import (
    calculate_deterministic_slippage_bps,
    calculate_cpmm_output_slippage_bps,
    max_leg_slippage_bps,
)
import apex_omega_core.core as core_pkg


# ---------------------------------------------------------------------------
# Package-level export guard
# ---------------------------------------------------------------------------

class TestCoreExports:
    """Confirm that core/__init__.py exports exactly the expected symbols from
    deterministic_slippage and does NOT export calculate_real_profit."""

    def test_calculate_deterministic_slippage_bps_exported(self):
        assert hasattr(core_pkg, "calculate_deterministic_slippage_bps")

    def test_calculate_cpmm_output_slippage_bps_exported(self):
        assert hasattr(core_pkg, "calculate_cpmm_output_slippage_bps")

    def test_max_leg_slippage_bps_exported(self):
        assert hasattr(core_pkg, "max_leg_slippage_bps")

    def test_calculate_real_profit_not_exported(self):
        """calculate_real_profit does not exist in deterministic_slippage;
        importing it would cause an ImportError. Ensure it is absent."""
        assert not hasattr(core_pkg, "calculate_real_profit")

    def test_all_includes_slippage_symbols(self):
        assert "calculate_deterministic_slippage_bps" in core_pkg.__all__
        assert "calculate_cpmm_output_slippage_bps" in core_pkg.__all__
        assert "max_leg_slippage_bps" in core_pkg.__all__


# ---------------------------------------------------------------------------
# calculate_deterministic_slippage_bps
# ---------------------------------------------------------------------------

class TestCalculateDeterministicSlippageBps:

    def test_zero_trade_size_returns_zero(self):
        assert calculate_deterministic_slippage_bps(0.0, 1_000_000.0) == 0.0

    def test_zero_pool_tvl_returns_zero(self):
        assert calculate_deterministic_slippage_bps(10_000.0, 0.0) == 0.0

    def test_v2_small_trade_low_impact(self):
        # $1k trade into a $1M pool — impact should be very small
        bps = calculate_deterministic_slippage_bps(1_000.0, 1_000_000.0)
        assert 0.0 < bps < 25.0

    def test_v2_large_trade_high_impact(self):
        # $100k trade into a $200k pool — impact should be substantial
        bps = calculate_deterministic_slippage_bps(100_000.0, 200_000.0)
        assert bps > 1_000.0

    def test_v2_known_value(self):
        # $10k trade, $1M TVL (reserve=500k), fee=30bps
        # effective_size = 10_000 * (1 - 0.003) = 9_970
        # impact = 1 - 500_000 / (500_000 + 9_970) ≈ 0.01955... → ~195.5 bps
        bps = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v2", fee_bps=30.0)
        assert abs(bps - 195.5) < 1.0

    def test_v3_higher_impact_than_v2_with_concentration(self):
        # More concentrated V3 range → smaller effective reserve → more impact
        bps_v2 = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v2")
        bps_v3 = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v3", v3_concentration=5.0)
        assert bps_v3 > bps_v2

    def test_v3_concentration_one_equals_v2(self):
        bps_v2 = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v2")
        bps_v3 = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v3", v3_concentration=1.0)
        assert abs(bps_v2 - bps_v3) < 1e-9

    def test_aerodrome_same_as_v2(self):
        bps_v2 = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="v2")
        bps_ae = calculate_deterministic_slippage_bps(10_000.0, 1_000_000.0, dex="aerodrome")
        assert abs(bps_v2 - bps_ae) < 1e-9

    def test_higher_fee_reduces_effective_size_and_impact(self):
        bps_low = calculate_deterministic_slippage_bps(50_000.0, 100_000.0, fee_bps=5.0)
        bps_high = calculate_deterministic_slippage_bps(50_000.0, 100_000.0, fee_bps=300.0)
        assert bps_low > bps_high

    def test_result_is_non_negative(self):
        for trade in [0.0, 1.0, 10_000.0, 500_000.0]:
            for tvl in [0.0, 10_000.0, 1_000_000.0]:
                assert calculate_deterministic_slippage_bps(trade, tvl) >= 0.0


# ---------------------------------------------------------------------------
# calculate_cpmm_output_slippage_bps
# ---------------------------------------------------------------------------

class TestCalculateCpmmOutputSlippageBps:

    def test_zero_no_impact_out_returns_zero(self):
        assert calculate_cpmm_output_slippage_bps(100.0, 0.0) == 0.0

    def test_negative_no_impact_out_returns_zero(self):
        assert calculate_cpmm_output_slippage_bps(100.0, -1.0) == 0.0

    def test_actual_equals_ideal_returns_zero(self):
        assert calculate_cpmm_output_slippage_bps(100.0, 100.0) == 0.0

    def test_1pct_shortfall_returns_100_bps(self):
        bps = calculate_cpmm_output_slippage_bps(99.0, 100.0)
        assert abs(bps - 100.0) < 1e-9

    def test_50pct_shortfall_returns_5000_bps(self):
        bps = calculate_cpmm_output_slippage_bps(50.0, 100.0)
        assert abs(bps - 5_000.0) < 1e-9

    def test_actual_exceeds_ideal_clamped_to_zero(self):
        # Should never happen in practice, but the function clamps at 0
        assert calculate_cpmm_output_slippage_bps(105.0, 100.0) == 0.0


# ---------------------------------------------------------------------------
# max_leg_slippage_bps
# ---------------------------------------------------------------------------

class TestMaxLegSlippageBps:

    def test_empty_legs_returns_zero(self):
        assert max_leg_slippage_bps([], trade_size_usd=10_000.0) == 0.0

    def test_single_leg_matches_direct_call(self):
        leg = {"venue": "v2", "pool_tvl_usd": 500_000.0, "fee": 0.003}
        result = max_leg_slippage_bps([leg], 10_000.0)
        expected = calculate_deterministic_slippage_bps(10_000.0, 500_000.0, dex="v2", fee_bps=30.0)
        assert abs(result - expected) < 1e-9

    def test_returns_worst_leg(self):
        legs = [
            {"venue": "v2", "pool_tvl_usd": 2_000_000.0, "fee": 0.003},
            {"venue": "v2", "pool_tvl_usd": 100_000.0, "fee": 0.003},  # shallow pool
        ]
        result = max_leg_slippage_bps(legs, 10_000.0)
        # Worst leg is the shallow $100k pool
        expected_shallow = calculate_deterministic_slippage_bps(10_000.0, 100_000.0, dex="v2", fee_bps=30.0)
        assert abs(result - expected_shallow) < 1e-9

    def test_v3_venue_string_mapped_correctly(self):
        leg_v3 = {"venue": "univ3_500", "pool_tvl_usd": 500_000.0, "fee": 0.0005}
        result = max_leg_slippage_bps([leg_v3], 10_000.0)
        expected = calculate_deterministic_slippage_bps(10_000.0, 500_000.0, dex="v3", fee_bps=5.0)
        assert abs(result - expected) < 1e-9

    def test_aerodrome_venue_mapped_correctly(self):
        leg_ae = {"venue": "aerodrome", "pool_tvl_usd": 300_000.0, "fee": 0.003}
        result = max_leg_slippage_bps([leg_ae], 10_000.0)
        expected = calculate_deterministic_slippage_bps(10_000.0, 300_000.0, dex="aerodrome", fee_bps=30.0)
        assert abs(result - expected) < 1e-9

    def test_fee_as_bps_value_above_1(self):
        # fee > 1.0 is treated as already-in-bps
        leg = {"venue": "v2", "pool_tvl_usd": 500_000.0, "fee": 30.0}
        result = max_leg_slippage_bps([leg], 10_000.0)
        expected = calculate_deterministic_slippage_bps(10_000.0, 500_000.0, dex="v2", fee_bps=30.0)
        assert abs(result - expected) < 1e-9
