"""Tests for mev_gas_oracle: GasOracle, PFillEstimator, TipOptimizer."""

import math
import pytest

from apex_omega_core.core.mev_gas_oracle import (
    FeeHistory,
    GasOracle,
    GasPriceSnapshot,
    PFillEstimator,
    TipOptimizer,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _snapshot(
    base_fee_gwei: float = 50.0,
    tip_p25: float = 1.0,
    tip_p50: float = 2.0,
    tip_p75: float = 4.0,
    tip_p90: float = 8.0,
    gas_used_ratio_avg: float = 0.6,
) -> GasPriceSnapshot:
    return GasPriceSnapshot(
        base_fee_gwei=base_fee_gwei,
        tip_p25_gwei=tip_p25,
        tip_p50_gwei=tip_p50,
        tip_p75_gwei=tip_p75,
        tip_p90_gwei=tip_p90,
        gas_used_ratio_avg=gas_used_ratio_avg,
    )


# ---------------------------------------------------------------------------
# GasPriceSnapshot
# ---------------------------------------------------------------------------

def test_gas_price_snapshot_fields() -> None:
    snap = _snapshot()
    assert snap.base_fee_gwei == 50.0
    assert snap.tip_p50_gwei == 2.0
    assert snap.tip_p90_gwei == 8.0
    assert snap.gas_used_ratio_avg == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# FeeHistory
# ---------------------------------------------------------------------------

def test_fee_history_structure() -> None:
    fh = FeeHistory(
        base_fee_per_gas=[100_000_000_000, 105_000_000_000],
        reward_percentiles=[[500_000_000, 1_000_000_000, 2_000_000_000, 4_000_000_000]],
        gas_used_ratio=[0.55],
        oldest_block=1_000_000,
    )
    assert len(fh.base_fee_per_gas) == 2
    assert fh.oldest_block == 1_000_000
    assert fh.reward_percentiles[0][1] == 1_000_000_000


# ---------------------------------------------------------------------------
# PFillEstimator
# ---------------------------------------------------------------------------

class TestPFillEstimator:

    def test_p_fill_at_median_is_half(self) -> None:
        snap = _snapshot(tip_p50=2.0, tip_p25=1.0, tip_p75=4.0)
        est = PFillEstimator(snap)
        # logistic(μ) = 0.5 exactly
        assert est.estimate(2.0) == pytest.approx(0.5, abs=1e-6)

    def test_p_fill_increases_with_tip(self) -> None:
        snap = _snapshot()
        est = PFillEstimator(snap)
        p_low = est.estimate(0.5)
        p_mid = est.estimate(2.0)
        p_high = est.estimate(10.0)
        assert p_low < p_mid < p_high

    def test_p_fill_bounded_zero_to_one(self) -> None:
        snap = _snapshot()
        est = PFillEstimator(snap)
        for tip in [-1.0, 0.0, 1.0, 2.0, 5.0, 100.0, 1e6]:
            p = est.estimate(tip)
            assert 0.0 <= p <= 1.0, f"P(fill) out of range for tip={tip}: {p}"

    def test_p_fill_at_p75_above_threshold(self) -> None:
        snap = _snapshot(tip_p25=1.0, tip_p50=2.0, tip_p75=4.0)
        est = PFillEstimator(snap)
        # At p75, P(fill) should exceed 0.85.
        assert est.estimate(4.0) > 0.85

    def test_sigma_nonzero_prevents_division_by_zero(self) -> None:
        # Same tip for p25 and p75 → spread = 0; sigma must be clamped to 0.05.
        snap = _snapshot(tip_p25=2.0, tip_p50=2.0, tip_p75=2.0)
        est = PFillEstimator(snap)
        assert est.sigma >= 0.05
        # Should not raise.
        p = est.estimate(2.0)
        assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# TipOptimizer
# ---------------------------------------------------------------------------

class TestTipOptimizer:

    def test_gas_cost_increases_with_tip(self) -> None:
        snap = _snapshot(base_fee_gwei=50.0)
        opt = TipOptimizer(snap, gas_units=350_000, chain="polygon")
        cost_low = opt.gas_cost_usd(0.0)
        cost_high = opt.gas_cost_usd(10.0)
        assert cost_high > cost_low

    def test_expected_profit_zero_when_gas_exceeds_net(self) -> None:
        snap = _snapshot(base_fee_gwei=50.0)
        opt = TipOptimizer(snap, gas_units=350_000, chain="polygon")
        # With p_net_usd = 0.0 gas always exceeds net → E[profit] = 0.
        assert opt.expected_profit(0.0, 2.0) == 0.0

    def test_optimal_tip_positive_for_positive_p_net(self) -> None:
        snap = _snapshot(base_fee_gwei=50.0)
        opt = TipOptimizer(snap, gas_units=350_000, chain="polygon")
        tip = opt.optimal_tip(p_net_usd=500.0)
        # Should select a non-negative tip.
        assert tip >= 0.0

    def test_optimal_tip_increases_with_p_net(self) -> None:
        snap = _snapshot()
        opt = TipOptimizer(snap, gas_units=350_000)
        tip_small = opt.optimal_tip(p_net_usd=50.0)
        tip_large = opt.optimal_tip(p_net_usd=5000.0)
        # Larger profit → worth paying higher tip for higher P(fill).
        assert tip_large >= tip_small

    def test_build_eip1559_params_keys(self) -> None:
        snap = _snapshot()
        opt = TipOptimizer(snap)
        params = opt.build_eip1559_params(p_net_usd=200.0)
        assert "maxPriorityFeePerGas" in params
        assert "maxFeePerGas" in params
        assert "tip_gwei" in params
        assert "base_fee_gwei" in params
        assert "p_fill" in params
        assert "expected_profit_usd" in params
        assert "gas_cost_usd" in params

    def test_build_eip1559_max_fee_covers_base_plus_tip(self) -> None:
        snap = _snapshot(base_fee_gwei=50.0)
        opt = TipOptimizer(snap)
        params = opt.build_eip1559_params(p_net_usd=300.0)
        tip_gwei = params["tip_gwei"]
        base_gwei = params["base_fee_gwei"]
        # max_fee = base × 2 + tip
        expected_max_fee_wei = int((base_gwei * 2.0 + tip_gwei) * 1e9)
        assert params["maxFeePerGas"] == pytest.approx(expected_max_fee_wei, rel=1e-6)

    def test_p_fill_in_eip1559_params_is_valid(self) -> None:
        snap = _snapshot()
        opt = TipOptimizer(snap)
        params = opt.build_eip1559_params(p_net_usd=1000.0)
        assert 0.0 <= params["p_fill"] <= 1.0

    def test_gas_cost_usd_correct_units(self) -> None:
        snap = _snapshot(base_fee_gwei=100.0)
        # gas_units=1_000_000, MATIC=$1 → cost = 1e6 * 100 * 1e-9 * 1 = $0.10
        opt = TipOptimizer(snap, gas_units=1_000_000, chain="polygon")
        opt.native_price_usd = 1.0
        cost = opt.gas_cost_usd(0.0)  # tip=0, so cost only from base_fee
        assert cost == pytest.approx(1_000_000 * 100.0 * 1e-9 * 1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# GasOracle – fallback path (offline, no RPC)
# ---------------------------------------------------------------------------

class TestGasOracleFallback:

    def _mock_w3(self, gas_price_wei: int = 100_000_000_000):
        """Return a minimal Web3-like mock that returns a fixed gas price."""
        from unittest.mock import MagicMock
        w3 = MagicMock()
        w3.eth.gas_price = gas_price_wei
        return w3

    def test_fallback_snapshot_fields_are_positive(self) -> None:
        oracle = GasOracle(w3=self._mock_w3(100_000_000_000))
        snap = oracle._fallback_snapshot()
        assert snap.base_fee_gwei > 0
        assert snap.tip_p25_gwei > 0
        assert snap.tip_p50_gwei >= snap.tip_p25_gwei
        assert snap.tip_p75_gwei >= snap.tip_p50_gwei
        assert snap.tip_p90_gwei >= snap.tip_p75_gwei

    def test_get_snapshot_falls_back_gracefully(self) -> None:
        oracle = GasOracle(w3=self._mock_w3(80_000_000_000))

        def _raise(*a, **kw):
            raise ConnectionError("offline")

        oracle.fetch_fee_history = _raise  # type: ignore[method-assign]
        snap = oracle.get_snapshot(force=True)
        assert isinstance(snap, GasPriceSnapshot)
        assert snap.base_fee_gwei > 0

    def test_get_snapshot_cached(self) -> None:
        call_count = {"n": 0}
        oracle = GasOracle(w3=self._mock_w3(60_000_000_000))

        def _raise(*a, **kw):
            call_count["n"] += 1
            raise ConnectionError("offline")

        oracle.fetch_fee_history = _raise  # type: ignore[method-assign]

        snap1 = oracle.get_snapshot(force=True)
        snap2 = oracle.get_snapshot()  # should hit cache
        assert snap1 is snap2
        assert call_count["n"] == 1  # fetch_fee_history called only once

    def test_invalidate_clears_cache(self) -> None:
        oracle = GasOracle(w3=self._mock_w3(60_000_000_000))

        def _raise(*a, **kw):
            raise ConnectionError("offline")

        oracle.fetch_fee_history = _raise  # type: ignore[method-assign]

        oracle.get_snapshot(force=True)
        oracle.invalidate()
        assert oracle._snapshot is None

    def test_build_snapshot_from_fee_history(self) -> None:
        from unittest.mock import MagicMock
        oracle = GasOracle(w3=MagicMock())
        fh = FeeHistory(
            # 20 blocks + next-block projection (index 20 = 55 Gwei)
            base_fee_per_gas=[int(50e9)] * 20 + [int(55e9)],
            reward_percentiles=[
                [int(1e9), int(2e9), int(4e9), int(8e9)]
                for _ in range(20)
            ],
            gas_used_ratio=[0.5] * 20,
            oldest_block=1_000_000,
        )
        snap = oracle._build_snapshot(fh)
        assert snap.base_fee_gwei == pytest.approx(55.0, rel=1e-6)
        assert snap.tip_p50_gwei == pytest.approx(2.0, rel=1e-3)
        assert snap.gas_used_ratio_avg == pytest.approx(0.5, rel=1e-6)
