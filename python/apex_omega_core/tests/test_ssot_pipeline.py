"""Tests for the full-stack SSOT pipeline."""

import random

import pytest

from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.ssot_pipeline import (
    BatchSimulator,
    BatchSummary,
    ExecutionDegradationSimulator,
    PipelineFinalResult,
    RouteAuditResult,
    SSOTPipelineFinalizer,
    audit_two_leg_route_envelope,
)


def _valid_audit_kwargs(**overrides):
    """Return kwargs for a well-formed 2-leg plan."""
    a_in = 1000.0
    b_out_1 = 995.0
    a_out_2 = 1008.5
    p_gross = a_out_2 - a_in
    c_total_exec = 1.0
    p_net = p_gross
    base = dict(
        a_in=a_in,
        fee1=0.003,
        b_out_1=b_out_1,
        b_in_2=b_out_1,
        fee2=0.0025,
        a_out_2=a_out_2,
        p_gross=p_gross,
        p_net=p_net,
        c_total_exec=c_total_exec,
    )
    base.update(overrides)
    return base


class TestAuditTwoLegRouteEnvelope:
    def test_valid_plan_passes(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs())
        assert isinstance(result, RouteAuditResult)
        assert result.passed is True
        assert result.violations == []

    def test_inventory_drift_detected(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(b_in_2=900.0))
        assert result.passed is False
        assert any("inventory_drift" in v for v in result.violations)

    def test_p_gross_mismatch_detected(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(p_gross=9999.0))
        assert result.passed is False
        assert any("p_gross_mismatch" in v for v in result.violations)

    def test_p_net_mismatch_detected(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(p_net=9999.0))
        assert result.passed is False
        assert any("p_net_mismatch" in v for v in result.violations)

    def test_fee1_out_of_range_detected(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(fee1=1.5))
        assert result.passed is False
        assert any("fee1_range" in v for v in result.violations)

    def test_fee2_negative_detected(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(fee2=-0.01))
        assert result.passed is False
        assert any("fee2_range" in v for v in result.violations)

    def test_multiple_violations_accumulated(self):
        result = audit_two_leg_route_envelope(**_valid_audit_kwargs(b_in_2=0.0, p_gross=9999.0))
        assert result.passed is False
        assert len(result.violations) >= 2

    def test_tolerance_boundary(self):
        kwargs = _valid_audit_kwargs()
        kwargs["b_in_2"] = kwargs["b_out_1"] + 1e-9
        assert audit_two_leg_route_envelope(**kwargs).passed is True

    def test_just_above_tolerance_triggers_violation(self):
        kwargs = _valid_audit_kwargs()
        kwargs["b_in_2"] = kwargs["b_out_1"] + 1e-7
        assert audit_two_leg_route_envelope(**kwargs).passed is False


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
            p_net_deterministic=8.5,
            c_total_exec=1.0,
            p_fill=0.9,
            c2_decision=c2_decision,
            fee1=0.003,
            fee2=0.0025,
        )

    def test_do_nothing_yields_zero_actual_profit(self):
        result = self._make_sim().simulate_one_run(**self._base_kwargs(c2_decision="DO_NOTHING"))
        assert result.p_net_actual == 0.0
        assert result.c2_decision == "DO_NOTHING"

    def test_strike_yields_non_negative_actual_profit(self):
        result = self._make_sim().simulate_one_run(**self._base_kwargs(c2_decision="STRIKE"))
        assert result.c2_decision == "STRIKE"
        assert result.p_net_actual >= 0.0

    def test_strike_audit_passes(self):
        assert self._make_sim().simulate_one_run(**self._base_kwargs()).audit.passed is True

    def test_do_nothing_audit_passes(self):
        result = self._make_sim().simulate_one_run(**self._base_kwargs(c2_decision="DO_NOTHING"))
        assert result.audit.passed is True

    def test_deterministic_with_same_seed(self):
        kwargs = self._base_kwargs()
        r1 = self._make_sim(seed=7).simulate_one_run(**kwargs)
        r2 = self._make_sim(seed=7).simulate_one_run(**kwargs)
        assert r1.p_net_actual == pytest.approx(r2.p_net_actual)

    def test_seeded_mean_degradation_converges(self):
        sim = self._make_sim(seed=0, mean=0.65, std=0.35)
        factors = [sim._sample_degradation_factor() for _ in range(10_000)]
        assert sum(factors) / len(factors) == pytest.approx(0.65, abs=0.05)

    def test_degradation_factor_never_negative(self):
        sim = self._make_sim(seed=99, mean=0.0, std=10.0)
        for _ in range(1000):
            assert sim._sample_degradation_factor() >= 0.0

    def test_result_fields_populated(self):
        result = self._make_sim().simulate_one_run(**self._base_kwargs())
        assert result.a_in == pytest.approx(1000.0)
        assert result.b_out_1 == pytest.approx(995.0)
        assert result.a_out_2 == pytest.approx(1008.5)
        assert result.p_gross_deterministic == pytest.approx(8.5)
        assert result.p_net_deterministic == pytest.approx(8.5)


class TestBatchSimulator:
    def _make_batch_sim(self, seed=42):
        return BatchSimulator(SlippageSentinel(), ExecutionDegradationSimulator(rng=random.Random(seed)))

    POOL_KWARGS = dict(
        a_in=5000.0,
        fee1=0.003,
        r1_in=2_000_000.0,
        r1_out=2_100_000.0,
        fee2=0.0025,
        r2_in=2_100_000.0,
        r2_out=2_050_000.0,
        c_total_exec=0.5,
        p_fill=0.9,
    )

    def test_returns_batch_summary(self):
        assert isinstance(self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=20), BatchSummary)

    def test_n_runs_matches(self):
        assert self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=50).n_runs == 50

    def test_n_strikes_consistent_with_c2_gate(self):
        assert self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=30).n_strikes == 30

    def test_hit_rate_between_zero_and_one(self):
        summary = self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=100)
        assert 0.0 <= summary.hit_rate <= 1.0

    def test_total_profit_equals_sum_of_mean_times_runs(self):
        summary = self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=100)
        assert summary.total_actual_profit == pytest.approx(
            summary.mean_actual_profit_per_run * summary.n_runs,
            rel=1e-9,
        )

    def test_ev_equals_owner_submission_edge_times_p_fill(self):
        math = SlippageSentinel().two_leg_arb_profit(
            a_in=5000.0,
            fee1=0.003,
            r1_in=2_000_000.0,
            r1_out=2_100_000.0,
            fee2=0.0025,
            r2_in=2_100_000.0,
            r2_out=2_050_000.0,
            c_gas=0.5,
        )
        summary = self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=1)
        assert summary.ev == pytest.approx(math["owner_submission_edge"] * 0.9, rel=1e-9)

    def test_do_nothing_when_p_fill_zero(self):
        kwargs = dict(self.POOL_KWARGS)
        kwargs["p_fill"] = 0.0
        summary = BatchSimulator(
            SlippageSentinel(),
            ExecutionDegradationSimulator(rng=random.Random(1)),
        ).run(**kwargs, n_runs=20)
        assert summary.n_strikes == 0
        assert summary.total_actual_profit == pytest.approx(0.0)

    def test_n_profitable_strikes_leq_n_strikes(self):
        summary = self._make_batch_sim().run(**self.POOL_KWARGS, n_runs=100)
        assert summary.n_profitable_strikes <= summary.n_strikes

    def test_seeded_reproducible(self):
        r1 = self._make_batch_sim(seed=123).run(**self.POOL_KWARGS, n_runs=50)
        r2 = self._make_batch_sim(seed=123).run(**self.POOL_KWARGS, n_runs=50)
        assert r1.total_actual_profit == pytest.approx(r2.total_actual_profit)
        assert r1.hit_rate == r2.hit_rate


class TestSSOTPipelineFinalizer:
    POOL_STATE = dict(
        fee1=0.003,
        r1_in=2_000_000.0,
        r1_out=2_100_000.0,
        fee2=0.0025,
        r2_in=2_100_000.0,
        r2_out=2_050_000.0,
        c_total_exec=0.5,
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
        assert isinstance(self._make_finalizer().run(**self.POOL_STATE), PipelineFinalResult)

    def test_best_size_is_from_sizes_to_test(self):
        assert self._make_finalizer().run(**self.POOL_STATE).best_size in self.SIZES

    def test_interior_optimum_not_always_largest_size(self):
        shallow = dict(
            fee1=0.003,
            r1_in=50_000.0,
            r1_out=52_500.0,
            fee2=0.0025,
            r2_in=52_500.0,
            r2_out=51_000.0,
            c_total_exec=0.1,
        )
        sizes = [100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0]
        result = SSOTPipelineFinalizer(
            sizes_to_test=sizes,
            n_batch_runs=10,
            p_fill=0.9,
            rng_seed=0,
        ).run(**shallow)
        assert result.best_size != max(sizes)

    def test_audit_passes_for_well_formed_plan(self):
        result = self._make_finalizer().run(**self.POOL_STATE)
        assert result.audit.passed is True
        assert result.audit.violations == []

    def test_c2_strike_when_profitable_and_p_fill_positive(self):
        assert self._make_finalizer(p_fill=0.9).run(**self.POOL_STATE).c2_decision == "STRIKE"

    def test_c2_do_nothing_when_p_fill_zero(self):
        result = SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=10,
            p_fill=0.0,
            rng_seed=0,
        ).run(**self.POOL_STATE)
        assert result.c2_decision == "DO_NOTHING"

    def test_ev_equals_owner_submission_edge_times_p_fill(self):
        p_fill = 0.75
        result = SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=10,
            p_fill=p_fill,
            rng_seed=0,
        ).run(**self.POOL_STATE)
        assert result.ev == pytest.approx(
            (result.p_net_deterministic - self.POOL_STATE["c_total_exec"]) * p_fill,
            rel=1e-9,
        )

    def test_batch_summary_has_correct_n_runs(self):
        assert self._make_finalizer(n_batch=75).run(**self.POOL_STATE).batch_summary.n_runs == 75

    def test_p_net_deterministic_is_positive(self):
        assert self._make_finalizer().run(**self.POOL_STATE).p_net_deterministic > 0.0

    def test_seeded_result_is_reproducible(self):
        r1 = self._make_finalizer(seed=999).run(**self.POOL_STATE)
        r2 = self._make_finalizer(seed=999).run(**self.POOL_STATE)
        assert r1.best_size == r2.best_size
        assert r1.p_net_deterministic == pytest.approx(r2.p_net_deterministic)
        assert r1.batch_summary.total_actual_profit == pytest.approx(r2.batch_summary.total_actual_profit)

    def test_empty_sizes_raises(self):
        with pytest.raises(ValueError, match="sizes_to_test"):
            SSOTPipelineFinalizer(sizes_to_test=[])

    def test_batch_hit_rate_in_range(self):
        result = self._make_finalizer(n_batch=200).run(**self.POOL_STATE)
        assert 0.0 <= result.batch_summary.hit_rate <= 1.0

    def test_total_profit_consistent_with_mean(self):
        bs = self._make_finalizer(n_batch=100).run(**self.POOL_STATE).batch_summary
        assert bs.total_actual_profit == pytest.approx(
            bs.mean_actual_profit_per_run * bs.n_runs,
            rel=1e-9,
        )

    def test_no_spread_produces_do_nothing(self):
        no_spread = dict(
            fee1=0.003,
            r1_in=1_000_000.0,
            r1_out=1_000_000.0,
            fee2=0.003,
            r2_in=1_000_000.0,
            r2_out=1_000_000.0,
            c_total_exec=0.01,
        )
        result = self._make_finalizer(p_fill=0.9).run(**no_spread)
        assert result.ev < 0.0
        assert result.c2_decision == "DO_NOTHING"


@pytest.mark.live
class TestSSOTPipelineWithLiveData:
    SIZES = [100.0, 500.0, 1_000.0, 5_000.0, 10_000.0, 50_000.0]

    def _make_finalizer(self, p_fill: float = 0.9, n_batch: int = 30) -> SSOTPipelineFinalizer:
        return SSOTPipelineFinalizer(
            sizes_to_test=self.SIZES,
            n_batch_runs=n_batch,
            p_fill=p_fill,
            rng_seed=42,
        )

    def test_pipeline_returns_final_result(self, live_pool_state):
        assert isinstance(self._make_finalizer().run(**live_pool_state), PipelineFinalResult)

    def test_best_size_is_from_candidates(self, live_pool_state):
        assert self._make_finalizer().run(**live_pool_state).best_size in self.SIZES

    def test_audit_passes_for_live_plan(self, live_pool_state):
        result = self._make_finalizer().run(**live_pool_state)
        assert isinstance(result.audit, RouteAuditResult)
        assert result.audit.passed is True, f"Audit failed with violations: {result.audit.violations}"

    def test_c2_decision_is_valid_string(self, live_pool_state):
        assert self._make_finalizer().run(**live_pool_state).c2_decision in {"STRIKE", "DO_NOTHING"}

    def test_ev_equals_owner_submission_edge_times_p_fill(self, live_pool_state):
        p_fill = 0.8
        result = self._make_finalizer(p_fill=p_fill).run(**live_pool_state)
        assert result.ev == pytest.approx(
            (result.p_net_deterministic - live_pool_state.get("c_total_exec", 0.0)) * p_fill,
            rel=1e-6,
        )

    def test_batch_summary_hit_rate_in_range(self, live_pool_state):
        result = self._make_finalizer(n_batch=50).run(**live_pool_state)
        assert 0.0 <= result.batch_summary.hit_rate <= 1.0

    def test_total_profit_consistent_with_mean(self, live_pool_state):
        bs = self._make_finalizer(n_batch=50).run(**live_pool_state).batch_summary
        assert bs.total_actual_profit == pytest.approx(
            bs.mean_actual_profit_per_run * bs.n_runs,
            rel=1e-9,
        )

    def test_live_pool_state_has_positive_reserves(self, live_pool_state):
        assert live_pool_state["r1_in"] > 0
        assert live_pool_state["r1_out"] > 0
        assert live_pool_state["r2_in"] > 0
        assert live_pool_state["r2_out"] > 0

    def test_live_pool_fees_are_valid(self, live_pool_state):
        assert 0 < live_pool_state["fee1"] < 1
        assert 0 < live_pool_state["fee2"] < 1

    def test_rpc_tester_endpoint_exported(self):
        from apex_omega_core.core import rpc_tester

        assert isinstance(rpc_tester.RPC_URL, str) and rpc_tester.RPC_URL
        assert isinstance(rpc_tester.WSS_URL, str)
