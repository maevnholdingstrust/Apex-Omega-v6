// rust/ssot_pipeline/src/finalizer.rs
//
// SSOT pipeline finalizer for the Dual Punch Phase 1 pipeline.

use crate::audit::audit_two_leg_route_envelope;
use crate::batch::BatchSimulator;
use crate::degradation::ExecutionDegradationSimulator;
use crate::math_core::two_leg_arb_profit;
use crate::types::PipelineFinalResult;

fn profitability_gate(p_net: f64, p_fill: f64) -> bool {
    p_net > 0.0 && p_fill > 0.0
}

/// Top-level entrypoint for the full 2-leg SSOT pipeline.
///
/// Steps:
/// 1. Best-size selection — evaluate each candidate via `two_leg_arb_profit`.
/// 2. Payload audit — verify canonical invariants on the best plan.
/// 3. C2 decision — STRIKE or DO_NOTHING via the profitability gate.
/// 4. Batch simulation — stress the pipeline over N probabilistic runs.
pub struct SSOTPipelineFinalizer {
    pub sizes_to_test: Vec<f64>,
    pub n_batch_runs: usize,
    pub p_fill: f64,
    pub degradation_mean: f64,
    pub degradation_std: f64,
    pub rng_seed: u64,
}

impl SSOTPipelineFinalizer {
    pub fn new(
        sizes_to_test: Vec<f64>,
        n_batch_runs: usize,
        p_fill: f64,
        degradation_mean: f64,
        degradation_std: f64,
        rng_seed: u64,
    ) -> Result<Self, String> {
        if sizes_to_test.is_empty() {
            return Err("sizes_to_test must contain at least one candidate size".to_string());
        }
        Ok(Self {
            sizes_to_test,
            n_batch_runs,
            p_fill,
            degradation_mean,
            degradation_std,
            rng_seed,
        })
    }

    /// Run the full pipeline for the given pool state.
    #[allow(clippy::too_many_arguments)]
    pub fn run(
        &self,
        fee1: f64,
        r1_in: f64,
        r1_out: f64,
        fee2: f64,
        r2_in: f64,
        r2_out: f64,
        c_total: f64,
    ) -> Result<PipelineFinalResult, String> {
        // ── Step 1: best-size selection ──────────────────────────────────────
        let mut best_size = f64::NEG_INFINITY;
        let mut best_p_net = f64::NEG_INFINITY;
        let mut best_math = None;

        for &size in &self.sizes_to_test {
            let math = two_leg_arb_profit(size, fee1, r1_in, r1_out, fee2, r2_in, r2_out, c_total, 0.0, 0.0);
            if math.p_net > best_p_net {
                best_p_net = math.p_net;
                best_size = size;
                best_math = Some(math);
            }
        }

        let best_math = best_math.ok_or_else(|| "No valid candidate size found in sizes_to_test".to_string())?;

        // ── Step 2: payload audit ────────────────────────────────────────────
        let audit = audit_two_leg_route_envelope(
            best_size,
            fee1,
            best_math.b_out_1,
            best_math.b_out_1, // b_in_2 IS b_out_1 (locked inventory identity)
            fee2,
            best_math.a_out_2,
            best_math.p_gross,
            best_math.p_net,
            c_total,
            1e-9,
        );

        // ── Step 3: C2 decision ──────────────────────────────────────────────
        let ev = best_p_net * self.p_fill;
        let c2_decision = if profitability_gate(best_p_net, self.p_fill) { "STRIKE" } else { "DO_NOTHING" };

        // ── Step 4: batch simulation ─────────────────────────────────────────
        let deg_sim = ExecutionDegradationSimulator::new(self.degradation_mean, self.degradation_std, self.rng_seed);
        let mut batch_sim = BatchSimulator::new(deg_sim);
        let batch_summary = batch_sim.run(
            best_size,
            fee1,
            r1_in,
            r1_out,
            fee2,
            r2_in,
            r2_out,
            c_total,
            self.p_fill,
            self.n_batch_runs,
        );

        Ok(PipelineFinalResult {
            best_size,
            p_net_deterministic: best_p_net,
            ev,
            c2_decision: c2_decision.to_string(),
            audit,
            batch_summary,
        })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn profitable_pools() -> (f64, f64, f64, f64, f64, f64) {
        (0.003, 100_000.0, 110_000.0, 0.003, 110_000.0, 105_000.0)
    }

    fn symmetric_pools() -> (f64, f64, f64, f64, f64, f64) {
        (0.003, 100_000.0, 100_000.0, 0.003, 100_000.0, 100_000.0)
    }

    #[test]
    fn empty_sizes_returns_err() {
        assert!(SSOTPipelineFinalizer::new(vec![], 10, 1.0, 0.65, 0.35, 42).is_err());
    }

    #[test]
    fn returns_ok_for_profitable_pools() {
        let f = SSOTPipelineFinalizer::new(vec![100.0, 500.0, 1000.0], 10, 1.0, 0.65, 0.35, 42).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = profitable_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 0.0);
        assert!(result.is_ok());
    }

    #[test]
    fn audit_passes_for_well_formed_plan() {
        let f = SSOTPipelineFinalizer::new(vec![100.0, 500.0, 1000.0], 10, 1.0, 0.65, 0.35, 42).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = profitable_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 0.0).unwrap();
        assert!(result.audit.passed, "violations: {:?}", result.audit.violations);
    }

    #[test]
    fn c2_strike_on_profitable_route() {
        let f = SSOTPipelineFinalizer::new(vec![100.0, 500.0, 1000.0], 10, 1.0, 0.65, 0.35, 42).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = profitable_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 0.0).unwrap();
        if result.p_net_deterministic > 0.0 {
            assert_eq!(result.c2_decision, "STRIKE");
        }
    }

    #[test]
    fn c2_do_nothing_on_unprofitable_route() {
        let f = SSOTPipelineFinalizer::new(vec![100.0, 500.0], 10, 1.0, 0.65, 0.35, 42).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = symmetric_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 1000.0).unwrap();
        assert_eq!(result.c2_decision, "DO_NOTHING");
    }

    #[test]
    fn batch_summary_n_runs_matches() {
        let f = SSOTPipelineFinalizer::new(vec![500.0, 1000.0], 25, 1.0, 0.65, 0.35, 7).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = profitable_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 0.0).unwrap();
        assert_eq!(result.batch_summary.n_runs, 25);
    }

    #[test]
    fn ev_equals_p_net_times_p_fill() {
        let p_fill = 0.8;
        let f = SSOTPipelineFinalizer::new(vec![100.0, 500.0], 5, p_fill, 0.65, 0.35, 1).unwrap();
        let (fee1, r1_in, r1_out, fee2, r2_in, r2_out) = profitable_pools();
        let result = f.run(fee1, r1_in, r1_out, fee2, r2_in, r2_out, 0.0).unwrap();
        assert!((result.ev - result.p_net_deterministic * p_fill).abs() < 1e-12);
    }
}
