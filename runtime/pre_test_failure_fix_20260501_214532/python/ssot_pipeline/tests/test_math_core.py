"""Tests for ssot_pipeline.math_core.

Coverage:
  - amm_swap: basic swap, zero inputs return 0.0, fee=0 case
  - two_leg_arb_profit: profit calculation, cost subtraction, dict keys
"""
import pytest

from ssot_pipeline.math_core import amm_swap, two_leg_arb_profit


class TestAmmSwap:
    def test_basic_swap(self):
        """Standard constant-product swap should return positive output."""
        out = amm_swap(100.0, 10_000.0, 10_000.0, 0.003)
        # A_eff = 100 * 0.997 = 99.7
        # out = (99.7 * 10000) / (10000 + 99.7) ≈ 98.715
        assert out == pytest.approx(99.7 * 10_000 / (10_000 + 99.7), rel=1e-9)

    def test_zero_reserve_in(self):
        assert amm_swap(100.0, 0.0, 10_000.0, 0.003) == 0.0

    def test_zero_reserve_out(self):
        assert amm_swap(100.0, 10_000.0, 0.0, 0.003) == 0.0

    def test_zero_amount_in(self):
        assert amm_swap(0.0, 10_000.0, 10_000.0, 0.003) == 0.0

    def test_zero_fee(self):
        """fee=0 means no fee applied."""
        out = amm_swap(100.0, 10_000.0, 10_000.0, 0.0)
        expected = (100.0 * 10_000) / (10_000 + 100.0)
        assert out == pytest.approx(expected, rel=1e-9)

    def test_full_fee_returns_zero(self):
        """fee=1.0 means entire input is taken as fee; effective input = 0."""
        out = amm_swap(100.0, 10_000.0, 10_000.0, 1.0)
        assert out == 0.0


class TestTwoLegArbProfit:
    def _symmetric_pools(self):
        """Pool parameters for a balanced arbitrage scenario."""
        return dict(
            a_in=1000.0,
            fee1=0.003,
            r1_in=100_000.0,
            r1_out=100_000.0,
            fee2=0.003,
            r2_in=100_000.0,
            r2_out=100_000.0,
        )

    def test_returns_expected_keys(self):
        result = two_leg_arb_profit(**self._symmetric_pools())
        assert set(result.keys()) == {"b_out_1", "a_out_2", "p_gross", "p_net", "owner_submission_edge"}

    def test_symmetric_pools_produce_loss(self):
        """On identical pools with fees, arbitrage is not profitable."""
        result = two_leg_arb_profit(**self._symmetric_pools())
        assert result["b_out_1"] > 0.0
        assert result["a_out_2"] > 0.0
        assert result["p_gross"] < 0.0  # fees consume the round-trip

    def test_profitable_arb(self):
        """A pool with a price discrepancy should yield positive p_gross."""
        result = two_leg_arb_profit(
            a_in=100.0,
            fee1=0.003,
            r1_in=1_000.0,
            r1_out=2_000.0,  # A is cheap on pool 1 (high B/A ratio)
            fee2=0.003,
            r2_in=2_000.0,
            r2_out=1_500.0,  # B is expensive on pool 2 (low A/B ratio)
        )
        assert result["p_gross"] > 0.0

    def test_c_gas_reduces_owner_submission_edge(self):
        params = self._symmetric_pools()
        result_no_cost = two_leg_arb_profit(**params)
        result_with_cost = two_leg_arb_profit(**params, c_gas=5.0)
        assert result_with_cost["p_net"] == pytest.approx(result_no_cost["p_net"], rel=1e-12)
        assert result_with_cost["owner_submission_edge"] == pytest.approx(
            result_no_cost["p_net"] - 5.0, rel=1e-12
        )

    def test_all_costs_additive(self):
        params = self._symmetric_pools()
        base = two_leg_arb_profit(**params)
        with_costs = two_leg_arb_profit(**params, c_gas=1.0, c_loan=2.0, c_other=0.5)
        assert with_costs["p_net"] == pytest.approx(base["p_net"] - 2.5, rel=1e-12)
        assert with_costs["owner_submission_edge"] == pytest.approx(base["p_net"] - 3.5, rel=1e-12)

    def test_flash_loan_fee_rate_computes_loan_cost_from_input(self):
        params = self._symmetric_pools()
        base = two_leg_arb_profit(**params)
        with_rate = two_leg_arb_profit(**params, flash_loan_fee_rate=0.0009)
        expected_loan_cost = params["a_in"] * 0.0009
        assert with_rate["p_net"] == pytest.approx(base["p_net"] - expected_loan_cost, rel=1e-12)

    def test_flash_loan_fee_rate_rejects_explicit_c_loan_double_count(self):
        params = self._symmetric_pools()
        with pytest.raises(ValueError, match="either c_loan or flash_loan_fee_rate"):
            two_leg_arb_profit(**params, c_loan=1.0, flash_loan_fee_rate=0.0009)

    def test_p_gross_equals_a_out2_minus_a_in(self):
        result = two_leg_arb_profit(
            a_in=500.0,
            fee1=0.002,
            r1_in=50_000.0,
            r1_out=55_000.0,
            fee2=0.002,
            r2_in=55_000.0,
            r2_out=50_000.0,
        )
        assert result["p_gross"] == pytest.approx(
            result["a_out_2"] - 500.0, rel=1e-12
        )

    def test_b_out_1_is_swap2_input(self):
        """b_out_1 feeds directly into swap 2; verifying internal consistency."""
        result = two_leg_arb_profit(
            a_in=100.0,
            fee1=0.003,
            r1_in=10_000.0,
            r1_out=10_000.0,
            fee2=0.003,
            r2_in=10_000.0,
            r2_out=10_000.0,
        )
        b_out_1 = result["b_out_1"]
        # Recompute a_out_2 from b_out_1 manually to confirm
        a_eff2 = b_out_1 * (1.0 - 0.003)
        expected_a_out_2 = (a_eff2 * 10_000) / (10_000 + a_eff2)
        assert result["a_out_2"] == pytest.approx(expected_a_out_2, rel=1e-12)
