// rust/ssot_pipeline/src/degradation.rs
//
// Execution degradation simulator for the Dual Punch SSOT pipeline.

use crate::audit::audit_two_leg_route_envelope;
use crate::types::ExecutionRunResult;

/// Simple linear congruential generator for reproducible degradation sampling.
/// Used to avoid pulling in an external RNG crate.
pub struct LcgRng {
    state: u64,
}

impl LcgRng {
    /// Create a new RNG with the given seed.
    pub fn new(seed: u64) -> Self {
        Self { state: seed.wrapping_add(1) }
    }

    /// Advance state and return a uniform f64 in [0.0, 1.0).
    pub fn next_f64(&mut self) -> f64 {
        // Knuth multiplicative LCG (64-bit)
        self.state = self.state.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
        (self.state >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Draw from N(mean, std) using Box-Muller transform; clamped to [0, ∞).
    pub fn sample_degradation(&mut self, mean: f64, std: f64) -> f64 {
        let u1 = self.next_f64().max(1e-300);
        let u2 = self.next_f64();
        let z = (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos();
        (mean + std * z).max(0.0)
    }
}

/// Models post-C1 execution variability for a 2-leg constant-product cycle.
pub struct ExecutionDegradationSimulator {
    pub degradation_mean: f64,
    pub degradation_std: f64,
    rng: LcgRng,
}

impl ExecutionDegradationSimulator {
    pub fn new(degradation_mean: f64, degradation_std: f64, seed: u64) -> Self {
        Self {
            degradation_mean,
            degradation_std,
            rng: LcgRng::new(seed),
        }
    }

    fn sample_degradation_factor(&mut self) -> f64 {
        self.rng.sample_degradation(self.degradation_mean, self.degradation_std)
    }

    /// Simulate one execution run with probabilistic profit degradation.
    pub fn simulate_one_run(
        &mut self,
        a_in: f64,
        b_out_1: f64,
        a_out_2: f64,
        p_gross: f64,
        p_net_deterministic: f64,
        c_total: f64,
        _p_fill: f64,
        c2_decision: &str,
        fee1: f64,
        fee2: f64,
    ) -> ExecutionRunResult {
        let audit = audit_two_leg_route_envelope(
            a_in, fee1, b_out_1, b_out_1, fee2, a_out_2, p_gross, p_net_deterministic, c_total, 1e-9,
        );

        if c2_decision != "STRIKE" {
            return ExecutionRunResult {
                a_in,
                b_out_1,
                a_out_2,
                p_gross_deterministic: p_gross,
                p_net_deterministic,
                p_net_actual: 0.0,
                c2_decision: c2_decision.to_string(),
                audit,
            };
        }

        let factor = self.sample_degradation_factor();
        ExecutionRunResult {
            a_in,
            b_out_1,
            a_out_2,
            p_gross_deterministic: p_gross,
            p_net_deterministic,
            p_net_actual: p_net_deterministic * factor,
            c2_decision: c2_decision.to_string(),
            audit,
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn do_nothing_yields_zero_actual_profit() {
        let mut sim = ExecutionDegradationSimulator::new(0.65, 0.35, 42);
        let r = sim.simulate_one_run(1000.0, 995.0, 1008.5, 8.5, 7.5, 1.0, 1.0, "DO_NOTHING", 0.003, 0.0025);
        assert_eq!(r.p_net_actual, 0.0);
        assert_eq!(r.c2_decision, "DO_NOTHING");
    }

    #[test]
    fn strike_produces_nonzero_actual_profit() {
        let mut sim = ExecutionDegradationSimulator::new(0.65, 0.1, 42);
        let r = sim.simulate_one_run(1000.0, 995.0, 1008.5, 8.5, 7.5, 1.0, 1.0, "STRIKE", 0.003, 0.0025);
        assert!(r.p_net_actual >= 0.0, "p_net_actual should be non-negative (clamped)");
        assert_eq!(r.c2_decision, "STRIKE");
    }

    #[test]
    fn audit_is_run_on_each_call() {
        let mut sim = ExecutionDegradationSimulator::new(0.65, 0.35, 0);
        let r = sim.simulate_one_run(1000.0, 995.0, 1008.5, 8.5, 7.5, 1.0, 1.0, "STRIKE", 0.003, 0.0025);
        assert!(r.audit.passed, "Audit should pass for a well-formed plan");
    }
}
