// rust/ssot_pipeline/src/batch.rs
//
// Batch simulator for the Dual Punch SSOT pipeline.

use crate::degradation::ExecutionDegradationSimulator;
use crate::math_core::two_leg_arb_profit;
use crate::types::BatchSummary;

fn profitability_gate(p_net: f64, p_fill: f64) -> bool {
    p_net > 0.0 && p_fill > 0.0
}

/// Stress-tests the C1→C2→execution pipeline over N probabilistic runs.
pub struct BatchSimulator {
    pub degradation_simulator: ExecutionDegradationSimulator,
}

impl BatchSimulator {
    pub fn new(degradation_simulator: ExecutionDegradationSimulator) -> Self {
        Self { degradation_simulator }
    }

    /// Run `n_runs` independent execution cycles for the given pool state.
    #[allow(clippy::too_many_arguments)]
    pub fn run(
        &mut self,
        a_in: f64,
        fee1: f64,
        r1_in: f64,
        r1_out: f64,
        fee2: f64,
        r2_in: f64,
        r2_out: f64,
        c_total: f64,
        p_fill: f64,
        n_runs: usize,
    ) -> BatchSummary {
        let math = two_leg_arb_profit(a_in, fee1, r1_in, r1_out, fee2, r2_in, r2_out, c_total, 0.0, 0.0);
        let p_net_det = math.p_net;
        let ev = p_net_det * p_fill;

        let c2_decision = if profitability_gate(p_net_det, p_fill) { "STRIKE" } else { "DO_NOTHING" };

        let mut total_actual_profit = 0.0_f64;
        let mut n_strikes = 0_usize;
        let mut n_profitable_strikes = 0_usize;

        for _ in 0..n_runs {
            let run_result = self.degradation_simulator.simulate_one_run(
                a_in,
                math.b_out_1,
                math.a_out_2,
                math.p_gross,
                p_net_det,
                c_total,
                p_fill,
                c2_decision,
                fee1,
                fee2,
            );
            total_actual_profit += run_result.p_net_actual;
            if c2_decision == "STRIKE" {
                n_strikes += 1;
                if run_result.p_net_actual > 0.0 {
                    n_profitable_strikes += 1;
                }
            }
        }

        let hit_rate = if n_strikes > 0 {
            n_profitable_strikes as f64 / n_strikes as f64
        } else {
            0.0
        };
        let mean_actual_profit_per_run = if n_runs > 0 {
            total_actual_profit / n_runs as f64
        } else {
            0.0
        };

        BatchSummary {
            n_runs,
            n_strikes,
            n_profitable_strikes,
            total_actual_profit,
            mean_actual_profit_per_run,
            hit_rate,
            ev,
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::degradation::ExecutionDegradationSimulator;

    fn make_sim() -> ExecutionDegradationSimulator {
        ExecutionDegradationSimulator::new(0.65, 0.35, 42)
    }

    #[test]
    fn symmetric_pools_produce_do_nothing() {
        let mut batch = BatchSimulator::new(make_sim());
        let summary = batch.run(1000.0, 0.003, 100_000.0, 100_000.0, 0.003, 100_000.0, 100_000.0, 0.0, 1.0, 10);
        assert_eq!(summary.n_strikes, 0);
        assert_eq!(summary.total_actual_profit, 0.0);
    }

    #[test]
    fn profitable_pools_produce_strikes() {
        let mut batch = BatchSimulator::new(make_sim());
        let summary = batch.run(100.0, 0.003, 1_000.0, 2_000.0, 0.003, 2_000.0, 1_500.0, 0.0, 1.0, 20);
        assert_eq!(summary.n_runs, 20);
        assert_eq!(summary.n_strikes, 20);
        assert!(summary.n_profitable_strikes > 0);
        assert!(summary.hit_rate > 0.0);
    }

    #[test]
    fn n_runs_matches_request() {
        let mut batch = BatchSimulator::new(make_sim());
        let summary = batch.run(1000.0, 0.003, 100_000.0, 100_000.0, 0.003, 100_000.0, 100_000.0, 0.0, 1.0, 50);
        assert_eq!(summary.n_runs, 50);
    }
}
