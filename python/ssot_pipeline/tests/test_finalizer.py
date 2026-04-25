"""Tests for ssot_pipeline.finalizer (SSOTPipelineFinalizer).

Coverage:
  - Interior optimum selection
  - Audit pass in well-formed pipeline run
  - C2 STRIKE decision for profitable route
  - C2 DO_NOTHING for unprofitable route
  - Batch summary shape and consistency
  - Empty sizes_to_test raises ValueError
"""
import pytest

from ssot_pipeline.finalizer import SSOTPipelineFinalizer
from ssot_pipeline.types import (
    BatchSummary,
    PipelineFinalResult,
    RouteAuditResult,
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------

def _profitable_pools():
    """Pool state where a small-size trade is profitable."""
    return dict(
        fee1=0.003,
        r1_in=100_000.0,
        r1_out=110_000.0,   # A is cheap on pool 1
        fee2=0.003,
        r2_in=110_000.0,
        r2_out=105_000.0,   # B is expensive on pool 2
    )


def _symmetric_pools():
    """Symmetric pools — fees make every round-trip a loss."""
    return dict(
        fee1=0.003,
        r1_in=100_000.0,
        r1_out=100_000.0,
        fee2=0.003,
        r2_in=100_000.0,
        r2_out=100_000.0,
    )


# ---------------------------------------------------------------------------
# SSOTPipelineFinalizer tests
# ---------------------------------------------------------------------------

class TestSSOTPipelineFinalizer:
    def test_empty_sizes_raises(self):
        with pytest.raises(ValueError, match="sizes_to_test"):
            SSOTPipelineFinalizer(sizes_to_test=[])

    def test_returns_pipeline_final_result(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[100.0, 500.0, 1000.0],
            n_batch_runs=10,
            rng_seed=42,
        )
        result = finalizer.run(**_profitable_pools())
        assert isinstance(result, PipelineFinalResult)

    def test_audit_passes_for_well_formed_pipeline(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[100.0, 500.0, 1000.0],
            n_batch_runs=10,
            rng_seed=42,
        )
        result = finalizer.run(**_profitable_pools())
        assert isinstance(result.audit, RouteAuditResult)
        assert result.audit.passed is True, f"Audit violations: {result.audit.violations}"

    def test_c2_strike_on_profitable_route(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[100.0, 500.0, 1000.0],
            n_batch_runs=10,
            p_fill=1.0,
            rng_seed=42,
        )
        result = finalizer.run(**_profitable_pools())
        if result.p_net_deterministic > 0.0:
            assert result.c2_decision == "STRIKE"

    def test_c2_do_nothing_on_unprofitable_route(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[100.0, 500.0],
            n_batch_runs=10,
            p_fill=1.0,
            rng_seed=42,
        )
        result = finalizer.run(**_symmetric_pools(), c_total=1000.0)
        assert result.c2_decision == "DO_NOTHING"

    def test_best_size_is_from_sizes_to_test(self):
        sizes = [100.0, 500.0, 1000.0, 2000.0]
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=sizes,
            n_batch_runs=5,
            rng_seed=0,
        )
        result = finalizer.run(**_profitable_pools())
        assert result.best_size in sizes

    def test_batch_summary_shape(self):
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[500.0, 1000.0],
            n_batch_runs=20,
            rng_seed=7,
        )
        result = finalizer.run(**_profitable_pools())
        bs = result.batch_summary
        assert isinstance(bs, BatchSummary)
        assert bs.n_runs == 20
        assert 0 <= bs.n_strikes <= 20
        assert 0 <= bs.n_profitable_strikes <= bs.n_strikes
        assert 0.0 <= bs.hit_rate <= 1.0

    def test_ev_equals_p_net_times_p_fill(self):
        p_fill = 0.8
        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=[100.0, 500.0],
            n_batch_runs=5,
            p_fill=p_fill,
            rng_seed=1,
        )
        result = finalizer.run(**_profitable_pools())
        assert result.ev == pytest.approx(
            result.p_net_deterministic * p_fill, rel=1e-12
        )

    def test_reproducible_with_seed(self):
        """Two runs with the same seed must produce identical batch stats."""
        def run_once():
            f = SSOTPipelineFinalizer(
                sizes_to_test=[100.0, 500.0, 1000.0],
                n_batch_runs=50,
                rng_seed=99,
            )
            return f.run(**_profitable_pools())

        r1 = run_once()
        r2 = run_once()
        assert r1.batch_summary.total_actual_profit == pytest.approx(
            r2.batch_summary.total_actual_profit, rel=1e-12
        )
