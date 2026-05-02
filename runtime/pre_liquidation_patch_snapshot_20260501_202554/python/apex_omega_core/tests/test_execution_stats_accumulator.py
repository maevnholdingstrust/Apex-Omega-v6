"""Unit tests for ExecutionStatsAccumulator (Feed E).

All tests run without a live RPC connection.
"""
from __future__ import annotations

import pytest

from apex_omega_core.core.execution_stats_accumulator import (
    ExecutionStatsAccumulator,
)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_default_window_size(self):
        acc = ExecutionStatsAccumulator()
        assert acc.window_size == 200

    def test_custom_window_size(self):
        acc = ExecutionStatsAccumulator(window_size=50)
        assert acc.window_size == 50

    def test_invalid_window_size_raises(self):
        with pytest.raises(ValueError):
            ExecutionStatsAccumulator(window_size=0)

    def test_sample_count_starts_at_zero(self):
        acc = ExecutionStatsAccumulator()
        assert acc.sample_count == 0


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_increments_sample_count(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(included=True)
        assert acc.sample_count == 1

    def test_record_respects_maxlen(self):
        acc = ExecutionStatsAccumulator(window_size=3)
        for _ in range(10):
            acc.record(included=True)
        assert acc.sample_count == 3

    def test_record_stores_all_fields(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(
            included=True,
            reverted=False,
            slippage_error_bps=12.5,
            pnl_error_bps=8.0,
            router="0xABC",
        )
        outcome = list(acc._outcomes)[0]
        assert outcome.included is True
        assert outcome.reverted is False
        assert outcome.slippage_error_bps == pytest.approx(12.5)
        assert outcome.pnl_error_bps == pytest.approx(8.0)
        assert outcome.router == "0xabc"  # lowercased

    def test_record_lowercases_router(self):
        acc = ExecutionStatsAccumulator(window_size=5)
        acc.record(included=True, router="0xABCDEF")
        assert list(acc._outcomes)[0].router == "0xabcdef"


# ---------------------------------------------------------------------------
# get_stats() — empty window
# ---------------------------------------------------------------------------

class TestGetStatsEmpty:
    def test_empty_window_returns_zero_stats(self):
        acc = ExecutionStatsAccumulator()
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(0.0)
        assert stats.revert_rate == pytest.approx(0.0)
        assert stats.route_hit_rate == pytest.approx(0.0)
        assert stats.realized_slippage_error_bps == pytest.approx(0.0)
        assert stats.expected_vs_actual_pnl_error_bps == pytest.approx(0.0)
        assert stats.per_router_failure_rates == {}

    def test_empty_window_p_exec_estimate_is_zero(self):
        acc = ExecutionStatsAccumulator()
        stats = acc.get_stats()
        assert stats.p_exec_estimate() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_stats() — inclusion rate
# ---------------------------------------------------------------------------

class TestInclusionRate:
    def test_all_included(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=True)
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(1.0)

    def test_none_included(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=False)
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(0.0)

    def test_half_included(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(3):
            acc.record(included=True)
        for _ in range(3):
            acc.record(included=False)
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# get_stats() — revert rate
# ---------------------------------------------------------------------------

class TestRevertRate:
    def test_no_reverts(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=True, reverted=False)
        stats = acc.get_stats()
        assert stats.revert_rate == pytest.approx(0.0)

    def test_all_reverted(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(3):
            acc.record(included=True, reverted=True)
        stats = acc.get_stats()
        assert stats.revert_rate == pytest.approx(1.0)

    def test_half_reverted(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(included=True, reverted=True)
        acc.record(included=True, reverted=False)
        stats = acc.get_stats()
        assert stats.revert_rate == pytest.approx(0.5)

    def test_reverted_not_included_counted_as_zero_revert_rate(self):
        # Reverts only count among *included* txs.
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=False, reverted=False)
        stats = acc.get_stats()
        # No included txs → revert_rate defaults to 0 (avoids zero-division).
        assert stats.revert_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_stats() — slippage and PnL error
# ---------------------------------------------------------------------------

class TestMeanErrors:
    def test_mean_slippage_error_bps(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(included=True, slippage_error_bps=10.0)
        acc.record(included=True, slippage_error_bps=20.0)
        acc.record(included=True, slippage_error_bps=30.0)
        stats = acc.get_stats()
        assert stats.realized_slippage_error_bps == pytest.approx(20.0)

    def test_mean_pnl_error_bps(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(included=True, pnl_error_bps=5.0)
        acc.record(included=True, pnl_error_bps=15.0)
        stats = acc.get_stats()
        assert stats.expected_vs_actual_pnl_error_bps == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# get_stats() — per-router failure rate
# ---------------------------------------------------------------------------

class TestPerRouterFailureRate:
    def test_single_router_all_success(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=True, reverted=False, router="0xrouter")
        stats = acc.get_stats()
        assert stats.per_router_failure_rates.get("0xrouter", -1) == pytest.approx(0.0)

    def test_single_router_all_failure(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(3):
            acc.record(included=False, router="0xrouter")
        stats = acc.get_stats()
        assert stats.per_router_failure_rates["0xrouter"] == pytest.approx(1.0)

    def test_two_routers_independent(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record(included=True, router="0xa")   # success
        acc.record(included=False, router="0xb")  # failure
        stats = acc.get_stats()
        assert stats.per_router_failure_rates["0xa"] == pytest.approx(0.0)
        assert stats.per_router_failure_rates["0xb"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# p_exec_estimate
# ---------------------------------------------------------------------------

class TestPExecEstimate:
    def test_all_included_no_reverts_gives_1(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(5):
            acc.record(included=True, reverted=False)
        stats = acc.get_stats()
        assert stats.p_exec_estimate() == pytest.approx(1.0)

    def test_half_included_no_reverts(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(3):
            acc.record(included=True, reverted=False)
        for _ in range(3):
            acc.record(included=False)
        stats = acc.get_stats()
        assert stats.p_exec_estimate() == pytest.approx(0.5)

    def test_all_included_all_reverted_gives_zero(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        for _ in range(4):
            acc.record(included=True, reverted=True)
        stats = acc.get_stats()
        assert stats.p_exec_estimate() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# record_from_invocation convenience wrapper
# ---------------------------------------------------------------------------

class TestRecordFromInvocation:
    def test_included_and_successful(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record_from_invocation(
            {"executed_onchain": True, "success": True},
            predicted_pnl_usd=100.0,
            realized_pnl_usd=98.0,
            router="0xr",
        )
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(1.0)
        assert stats.revert_rate == pytest.approx(0.0)
        # pnl_error_bps = |100 - 98| / 100 * 10_000 = 200 bps
        assert stats.expected_vs_actual_pnl_error_bps == pytest.approx(200.0)

    def test_included_but_reverted(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record_from_invocation(
            {"executed_onchain": True, "success": False},
            router="0xr",
        )
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(1.0)
        assert stats.revert_rate == pytest.approx(1.0)

    def test_not_included(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record_from_invocation(
            {"executed_onchain": False, "success": False},
        )
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(0.0)

    def test_zero_predicted_pnl_gives_zero_error(self):
        acc = ExecutionStatsAccumulator(window_size=10)
        acc.record_from_invocation(
            {"executed_onchain": True, "success": True},
            predicted_pnl_usd=0.0,
            realized_pnl_usd=50.0,
        )
        stats = acc.get_stats()
        assert stats.expected_vs_actual_pnl_error_bps == pytest.approx(0.0)

    def test_rolling_window_evicts_oldest(self):
        acc = ExecutionStatsAccumulator(window_size=3)
        # First 3: all failures
        for _ in range(3):
            acc.record_from_invocation({"executed_onchain": False, "success": False})
        # Next 3: all successes — should evict the failures
        for _ in range(3):
            acc.record_from_invocation({"executed_onchain": True, "success": True})
        stats = acc.get_stats()
        assert stats.inclusion_rate == pytest.approx(1.0)
