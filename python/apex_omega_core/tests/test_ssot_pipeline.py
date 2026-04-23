"""Tests for the full-stack SSOT pipeline (ssot_pipeline.py).

Coverage:
  - audit_two_leg_route_envelope: valid plan, each individual violation
  - ExecutionDegradationSimulator: DO_NOTHING path, STRIKE path, seeded stats
  - BatchSimulator: strike counts, hit rate, total profit consistency
  - SSOTPipelineFinalizer: interior optimum, audit pass, C2 decision, batch shape
"""
import random

import pytest

from apex_omega_core.core.ssot_pipeline import (
    BatchSimulator,
    BatchSummary,
    ExecutionDegradationSimulator,
    PipelineFinalResult,
    RouteAuditResult,
    SSOTPipelineFinalizer,
    audit_two_leg_route_envelope,
)
from apex_omega_core.core.slippage_sentinel import SlippageSentinel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_audit_kwargs(**overrides):
    """Return kwargs for a well-formed 2-leg plan.  Override individual fields
    to inject specific violations."""
    a_in = 1000.0
    b_out_1 = 995.0
    a_out_2 = 1008.5
    p_gross = a_out_2 - a_in   # = 8.5
    c_total = 1.0
    p_net = p_gross - c_total  # = 7.5
    base = dict(
        a_in=a_in,
        fee1=0.003,
        b_out_1=b_out_1,
        b_in_2=b_out_1,
        fee2=0.0025,
        a_out_2=a_out_2,
        p_gross=p_gross,
        p_net=p_net,
        c_total=c_total,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# audit_two_leg_route_envelope
# ---------------------------------------------------------------------------

class TestAuditTwoLegRouteEnvelope:
    def test_valid_plan_passes(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs())
        assert isinstance(result, RouteAuditResult)
        assert result.passed is True
        assert result.violations == []

    def test_inventory_drift_detected(self):
        """b_in_2 != b_out_1 must produce an inventory_drift violation."""
        kwargs = _valid_audit_kwargs(b_in_2=900.0)  # differs from b_out_1=995.0
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert any("inventory_drift" in v for v in result.violations)

    def test_p_gross_mismatch_detected(self):
        """p_gross that does not equal a_out_2 - a_in must be flagged."""
        # Introduce a small deliberate error (> tolerance)
        kwargs = _valid_audit_kwargs(p_gross=9999.0)
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert any("p_gross_mismatch" in v for v in result.violations)

    def test_p_net_mismatch_detected(self):
        """p_net that does not equal p_gross - c_total must be flagged."""
        kwargs = _valid_audit_kwargs(p_net=9999.0)
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert any("p_net_mismatch" in v for v in result.violations)

    def test_fee1_out_of_range_detected(self):
        """fee1 >= 1.0 must be flagged."""
        kwargs = _valid_audit_kwargs(fee1=1.5)
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert any("fee1_range" in v for v in result.violations)

    def test_fee2_negative_detected(self):
        """fee2 < 0 must be flagged."""
        kwargs = _valid_audit_kwargs(fee2=-0.01)
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert any("fee2_range" in v for v in result.violations)

    def test_multiple_violations_accumulated(self):
        """Both b_in_2 drift and p_gross mismatch are reported simultaneously."""
        kwargs = _valid_audit_kwargs(b_in_2=0.0, p_gross=9999.0)
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False
        assert len(result.violations) >= 2

    def test_tolerance_boundary(self):
        """A drift exactly at the tolerance threshold must not trigger a violation."""
        kwargs = _valid_audit_kwargs()
        # Drift of exactly 1e-9 (= tolerance) should not violate
        b = kwargs["b_out_1"]
        kwargs["b_in_2"] = b + 1e-9
        result = audit_two_leg_route_envelope(**kwargs)
        # 1e-9 == tolerance, abs difference == tolerance → not > tolerance → pass
        assert result.passed is True

    def test_just_above_tolerance_triggers_violation(self):
        """A drift clearly above the tolerance threshold must trigger a violation."""
        kwargs = _valid_audit_kwargs()
        b = kwargs["b_out_1"]
        # 1e-9 is the tolerance; adding 1e-7 is unambiguously above it at float64
        kwargs["b_in_2"] = b + 1e-7
        result = audit_two_leg_route_envelope(**kwargs)
        assert result.passed is False


# ---------------------------------------------------------------------------
# ExecutionDegradationSimulator
# ---------------------------------------------------------------------------

class TestExecutionDegradationSimulator:
    def _make_sim(self, seed=42, mean=0.65, std=0.35):
        return ExecutionDegradationSimulator(
            degradation_mean=mean,
            degradation_std=std,
            rng=random.Random(seed),
        )

    def _base_kwargs(self, c2_decision="STRIKE"):
        return dict(
            a_in=1000.0,
            b_out_1=995.0,
            a_out_2=1008.5,
            p_gross=8.5,
            p_net_deterministic=7.5,
            c_total=1.0,
            p_fill=0.9,
            c2_decision=c2_decision,
            fee1=0.003,
            fee2=0.0025,
        )

    def test_do_nothing_yields_zero_actual_profit(self):
        sim = self._make_sim()
        result = sim.simulate_one_run(**self._base_kwargs(c2_decision="DO_NOTHING"))
        assert result.p_net_actual == 0.0
        assert result.c2_decision == "DO_NOTHING"

    def test_strike_yields_non_negative_actual_profit(self):
        """Degradation factor is clamped to >= 0, so p_net_actual >= 0."""
        sim = self._make_sim()
        result = sim.simulate_one_run(**self._base_kwargs(c2_decision="STRIKE"))
        assert result.c2_decision == "STRIKE"
        assert result.p_net_actual >= 0.0

    def test_strike_audit_passes(self):
        """The route envelope audit must pass for a correctly constructed plan."""
        sim = self._make_sim()
        result = sim.simulate_one_run(**self._base_kwargs(c2_decision="STRIKE"))
        assert result.audit.passed is True

    def test_do_nothing_audit_passes(self):
        sim = self._make_sim()
        result = sim.simulate_one_run(**self._base_kwargs(c2_decision="DO_NOTHING"))
        assert result.audit.passed is True

    def test_deterministic_with_same_seed(self):
        """Two simulators with the same seed must produce identical results."""
        kwargs = self._base_kwargs()
        r1 = self._make_sim(seed=7).simulate_one_run(**kwargs)
        r2 = self._make_sim(seed=7).simulate_one_run(**kwargs)
        assert r1.p_net_actual == pytest.approx(r2.p_net_actual)

    def test_seeded_mean_degradation_converges(self):
        """Over 10000 samples, mean factor should be close to degradation_mean."""
        sim = self._make_sim(seed=0, mean=0.65, std=0.35)
        factors = [sim._sample_degradation_factor() for _ in range(10_000)]
        # Allow ±5% tolerance on the sample mean (actual > mean due to zero-floor clamping)
        assert sum(factors) / len(factors) == pytest.approx(0.65, abs=0.05)

    def test_degradation_factor_never_negative(self):
        """Clamping must ensure no negative degradation factors."""
        sim = self._make_sim(seed=99, mean=0.0, std=10.0)  # extreme spread
        for _ in range(1000):
            assert sim._sample_degradation_factor() >= 0.0

    def test_result_fields_populated(self):
        sim = self._make_sim()
        result = sim.simulate_one_run(**self._base_kwargs())
        assert result.a_in == pytest.approx(1000.0)
        assert result.b_out_1 == pytest.approx(995.0)
        assert result.a_out_2 == pytest.approx(1008.5)
        assert result.p_gross_deterministic == pytest.approx(8.5)
        assert result.p_net_deterministic == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# BatchSimulator
# ---------------------------------------------------------------------------

class TestBatchSimulator:
    def _make_batch_sim(self, seed=42):
        sentinel = SlippageSentinel()
        deg_sim = ExecutionDegradationSimulator(rng=random.Random(seed))
        return BatchSimulator(sentinel, deg_sim)

    # Deep-liquidity pool with a real spread so C1 profit > 0.
    POOL_KWARGS = dict(
        a_in=5000.0,
        fee1=0.003,
        r1_in=2_000_000.0,
        r1_out=2_100_000.0,   # pool 1 has asset-B slightly cheaper
        fee2=0.0025,
        r2_in=2_100_000.0,
        r2_out=2_050_000.0,   # pool 2 prices asset-A slightly higher
        c_total=0.5,
        p_fill=0.9,
    )

    def test_returns_batch_summary(self):
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=20)
        assert isinstance(summary, BatchSummary)

    def test_n_runs_matches(self):
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=50)
        assert summary.n_runs == 50

    def test_n_strikes_consistent_with_c2_gate(self):
        """With p_fill=0.9 and a profitable trade, every run should strike."""
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=30)
        # C2 decision is deterministic; with a real spread and p_fill=0.9 every run strikes
        assert summary.n_strikes == 30

    def test_hit_rate_between_zero_and_one(self):
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=100)
        assert 0.0 <= summary.hit_rate <= 1.0

    def test_total_profit_equals_sum_of_mean_times_runs(self):
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=100)
        assert summary.total_actual_profit == pytest.approx(
            summary.mean_actual_profit_per_run * summary.n_runs, rel=1e-9
        )

    def test_ev_equals_p_net_times_p_fill(self):
        """EV = p_net_deterministic × p_fill."""
        sentinel = SlippageSentinel()
        math = sentinel.two_leg_arb_profit(
            a_in=5000.0,
            fee1=0.003,
            r1_in=2_000_000.0,
            r1_out=2_100_000.0,
            fee2=0.0025,
            r2_in=2_100_000.0,
            r2_out=2_050_000.0,
            c_gas=0.5,
        )
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=1)
        assert summary.ev == pytest.approx(math["p_net"] * 0.9, rel=1e-9)

    def test_do_nothing_when_p_fill_zero(self):
        """p_fill=0 forces DO_NOTHING; all n_strikes must be 0."""
        sentinel = SlippageSentinel()
        deg_sim = ExecutionDegradationSimulator(rng=random.Random(1))
        sim = BatchSimulator(sentinel, deg_sim)
        kwargs = dict(self.POOL_KWARGS)
        kwargs["p_fill"] = 0.0
        summary = sim.run(**kwargs, n_runs=20)
        assert summary.n_strikes == 0
        assert summary.total_actual_profit == pytest.approx(0.0)

    def test_n_profitable_strikes_leq_n_strikes(self):
        sim = self._make_batch_sim()
        summary = sim.run(**self.POOL_KWARGS, n_runs=100)
        assert summary.n_profitable_strikes <= summary.n_strikes

    def test_seeded_reproducible(self):
        """Same seed must produce identical batch summaries."""
        s1 = self._make_batch_sim(seed=123)
        s2 = self._make_batch_sim(seed=123)
        r1 = s1.run(**self.POOL_KWARGS, n_runs=50)
        r2 = s2.run(**self.POOL_KWARGS, n_runs=50)
        assert r1.total_actual_profit == pytest.approx(r2.total_actual_profit)
        assert r1.hit_rate == r2.hit_rate


# ---------------------------------------------------------------------------
# SSOTPipelineFinalizer
# ---------------------------------------------------------------------------

class TestSSOTPipelineFinalizer:
    # Pool with a clear arbitrage spread so the pipeline can find a real profit.
    POOL_STATE = dict(
        fee1=0.003,
        r1_in=2_000_000.0,
        r1_out=2_100_000.0,
        fee2=0.0025,
        r2_in=2_100_000.0,
        r2_out=2_050_000.0,
        c_total=0.5,
    )
    SIZES = [500.0, 1000.0, 2000.0, 3000.0, 5000.0, 8000.0, 12000.0]

    def _make_finalizer(self, seed=42, p_fill=0.9, n_batch=50):
        return SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=n_batch,
            p_fill=p_fill,
            rng_seed=seed,
        )

    def test_returns_pipeline_final_result(self):
        finalizer = self._make_finalizer()
        result = finalizer.run(**self.POOL_STATE)
        assert isinstance(result, PipelineFinalResult)

    def test_best_size_is_from_sizes_to_test(self):
        finalizer = self._make_finalizer()
        result = finalizer.run(**self.POOL_STATE)
        assert result.best_size in self.SIZES

    def test_interior_optimum_not_always_largest_size(self):
        """For deep pools with noticeable slippage, the optimum must not be the
        largest size in the list.  The constant-product formula makes profit
        concave in trade size."""
        # Use a shallower pool to amplify slippage
        shallow = dict(
            fee1=0.003,
            r1_in=50_000.0,
            r1_out=52_500.0,
            fee2=0.0025,
            r2_in=52_500.0,
            r2_out=51_000.0,
            c_total=0.1,
        )
        sizes = [100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0]
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=sizes,
            n_batch_runs=10,
            p_fill=0.9,
            rng_seed=0,
        )
        result = finalizer.run(**shallow)
        assert result.best_size != max(sizes), (
            "Interior optimum expected due to slippage curvature, but best_size "
            "equals max(sizes). Verify pool configuration produces concave profit function."
        )

    def test_audit_passes_for_well_formed_plan(self):
        finalizer = self._make_finalizer()
        result = finalizer.run(**self.POOL_STATE)
        assert result.audit.passed is True
        assert result.audit.violations == []

    def test_c2_strike_when_profitable_and_p_fill_positive(self):
        finalizer = self._make_finalizer(p_fill=0.9)
        result = finalizer.run(**self.POOL_STATE)
        # With a real spread and p_fill=0.9, C2 must decide STRIKE
        assert result.c2_decision == "STRIKE"

    def test_c2_do_nothing_when_p_fill_zero(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=10,
            p_fill=0.0,
            rng_seed=0,
        )
        result = finalizer.run(**self.POOL_STATE)
        assert result.c2_decision == "DO_NOTHING"

    def test_ev_equals_p_net_times_p_fill(self):
        p_fill = 0.75
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=10,
            p_fill=p_fill,
            rng_seed=0,
        )
        result = finalizer.run(**self.POOL_STATE)
        assert result.ev == pytest.approx(result.p_net_deterministic * p_fill, rel=1e-9)

    def test_batch_summary_has_correct_n_runs(self):
        finalizer = self._make_finalizer(n_batch=75)
        result = finalizer.run(**self.POOL_STATE)
        assert result.batch_summary.n_runs == 75

    def test_p_net_deterministic_is_positive(self):
        finalizer = self._make_finalizer()
        result = finalizer.run(**self.POOL_STATE)
        assert result.p_net_deterministic > 0.0

    def test_seeded_result_is_reproducible(self):
        f1 = self._make_finalizer(seed=999)
        f2 = self._make_finalizer(seed=999)
        r1 = f1.run(**self.POOL_STATE)
        r2 = f2.run(**self.POOL_STATE)
        assert r1.best_size == r2.best_size
        assert r1.p_net_deterministic == pytest.approx(r2.p_net_deterministic)
        assert r1.batch_summary.total_actual_profit == pytest.approx(
            r2.batch_summary.total_actual_profit
        )

    def test_empty_sizes_raises(self):
        with pytest.raises(ValueError, match="sizes_to_test"):
            SSOTPipelineFinalizer(sizes_to_test=[])

    def test_batch_hit_rate_in_range(self):
        finalizer = self._make_finalizer(n_batch=200)
        result = finalizer.run(**self.POOL_STATE)
        assert 0.0 <= result.batch_summary.hit_rate <= 1.0

    def test_total_profit_consistent_with_mean(self):
        finalizer = self._make_finalizer(n_batch=100)
        result = finalizer.run(**self.POOL_STATE)
        bs = result.batch_summary
        assert bs.total_actual_profit == pytest.approx(
            bs.mean_actual_profit_per_run * bs.n_runs, rel=1e-9
        )

    def test_no_spread_produces_do_nothing(self):
        """Symmetric pools with no spread should yield p_net <= 0 → DO_NOTHING."""
        no_spread = dict(
            fee1=0.003,
            r1_in=1_000_000.0,
            r1_out=1_000_000.0,
            fee2=0.003,
            r2_in=1_000_000.0,
            r2_out=1_000_000.0,
            c_total=0.01,
        )
        finalizer = self._make_finalizer(p_fill=0.9)
        result = finalizer.run(**no_spread)
        # With a positive c_total and symmetric pools, net profit must be negative
        assert result.p_net_deterministic < 0.0
        assert result.c2_decision == "DO_NOTHING"


# ---------------------------------------------------------------------------
# Live-data tests  (require a live Polygon RPC connection)
#
# Run:     pytest -m live
# Skip:    pytest -m "not live"  (default in CI without network)
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestSSOTPipelineWithLiveData:
    """Integration tests that feed real on-chain pool state into the SSOT
    pipeline.  All pool data originates from the live Polygon RPC endpoint
    configured in ``apex_omega_core/.env`` via ``rpc_tester``.

    These tests validate structural correctness of pipeline outputs (types,
    field ranges, invariants) rather than asserting specific profit values,
    because on-chain reserves change block-by-block.
    """

    SIZES = [100.0, 500.0, 1_000.0, 5_000.0, 10_000.0, 50_000.0]

    def _make_finalizer(self, p_fill: float = 0.9, n_batch: int = 30) -> SSOTPipelineFinalizer:
        return SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=n_batch,
            p_fill=p_fill,
            rng_seed=42,
        )

    def test_pipeline_returns_final_result(self, live_pool_state):
        """Pipeline must return a PipelineFinalResult from live pool state."""
        finalizer = self._make_finalizer()
        result = finalizer.run(**live_pool_state)
        assert isinstance(result, PipelineFinalResult)

    def test_best_size_is_from_candidates(self, live_pool_state):
        """best_size must be one of the candidate sizes passed to the finalizer."""
        finalizer = self._make_finalizer()
        result = finalizer.run(**live_pool_state)
        assert result.best_size in self.SIZES

    def test_audit_passes_for_live_plan(self, live_pool_state):
        """Route envelope audit must pass for a correctly assembled live plan."""
        finalizer = self._make_finalizer()
        result = finalizer.run(**live_pool_state)
        assert isinstance(result.audit, RouteAuditResult)
        assert result.audit.passed is True, (
            f"Audit failed with violations: {result.audit.violations}"
        )

    def test_c2_decision_is_valid_string(self, live_pool_state):
        """c2_decision must be one of the two valid outcome strings."""
        finalizer = self._make_finalizer()
        result = finalizer.run(**live_pool_state)
        assert result.c2_decision in {"STRIKE", "DO_NOTHING"}

    def test_ev_equals_p_net_times_p_fill(self, live_pool_state):
        """EV invariant: ev == p_net_deterministic × p_fill."""
        p_fill = 0.8
        finalizer = self._make_finalizer(p_fill=p_fill)
        result = finalizer.run(**live_pool_state)
        assert result.ev == pytest.approx(result.p_net_deterministic * p_fill, rel=1e-9)

    def test_batch_summary_hit_rate_in_range(self, live_pool_state):
        """hit_rate must always be in [0, 1]."""
        finalizer = self._make_finalizer(n_batch=50)
        result = finalizer.run(**live_pool_state)
        assert 0.0 <= result.batch_summary.hit_rate <= 1.0

    def test_total_profit_consistent_with_mean(self, live_pool_state):
        """total_actual_profit == mean_actual_profit_per_run × n_runs."""
        finalizer = self._make_finalizer(n_batch=50)
        result = finalizer.run(**live_pool_state)
        bs = result.batch_summary
        assert bs.total_actual_profit == pytest.approx(
            bs.mean_actual_profit_per_run * bs.n_runs, rel=1e-9
        )

    def test_live_pool_state_has_positive_reserves(self, live_pool_state):
        """Sanity-check: rpc_tester must return positive non-zero reserves."""
        assert live_pool_state["r1_in"] > 0, "r1_in must be positive"
        assert live_pool_state["r1_out"] > 0, "r1_out must be positive"
        assert live_pool_state["r2_in"] > 0, "r2_in must be positive"
        assert live_pool_state["r2_out"] > 0, "r2_out must be positive"

    def test_live_pool_fees_are_valid(self, live_pool_state):
        """Fees from the live pool state must be in the valid (0, 1) range."""
        assert 0 < live_pool_state["fee1"] < 1, f"fee1={live_pool_state['fee1']!r} out of range"
        assert 0 < live_pool_state["fee2"] < 1, f"fee2={live_pool_state['fee2']!r} out of range"

    def test_rpc_tester_endpoint_exported(self):
        """rpc_tester.RPC_URL and WSS_URL must be non-empty strings."""
        from apex_omega_core.core import rpc_tester

        assert isinstance(rpc_tester.RPC_URL, str) and rpc_tester.RPC_URL
        assert isinstance(rpc_tester.WSS_URL, str)  # may be empty if not configured
