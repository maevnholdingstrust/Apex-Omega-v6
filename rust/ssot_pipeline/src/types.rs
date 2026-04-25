// rust/ssot_pipeline/src/types.rs
//
// Data containers for the Dual Punch SSOT pipeline (Phase 1).

use serde::{Deserialize, Serialize};

/// Result of a single route envelope audit pass.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteAuditResult {
    /// `true` iff all canonical invariants were satisfied.
    pub passed: bool,
    /// Human-readable descriptions of every invariant that failed.
    pub violations: Vec<String>,
}

/// Result of one simulated execution run.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExecutionRunResult {
    pub a_in: f64,
    pub b_out_1: f64,
    pub a_out_2: f64,
    pub p_gross_deterministic: f64,
    pub p_net_deterministic: f64,
    /// Realized profit after execution degradation.  Zero when C2 chose DO_NOTHING.
    pub p_net_actual: f64,
    /// "STRIKE" or "DO_NOTHING"
    pub c2_decision: String,
    pub audit: RouteAuditResult,
}

/// Aggregated results from a batch simulation.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BatchSummary {
    pub n_runs: usize,
    pub n_strikes: usize,
    pub n_profitable_strikes: usize,
    pub total_actual_profit: f64,
    pub mean_actual_profit_per_run: f64,
    /// n_profitable_strikes / n_strikes; 0.0 when n_strikes == 0.
    pub hit_rate: f64,
    /// Expected value per cycle = p_net_deterministic × p_fill.
    pub ev: f64,
}

/// Complete output of the SSOT pipeline finalizer.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PipelineFinalResult {
    /// The input amount (asset A) that yielded the highest net profit.
    pub best_size: f64,
    /// Net profit at the best size, before execution degradation.
    pub p_net_deterministic: f64,
    /// Expected value = p_net_deterministic × p_fill.
    pub ev: f64,
    /// "STRIKE" or "DO_NOTHING" based on the EV gate.
    pub c2_decision: String,
    pub audit: RouteAuditResult,
    pub batch_summary: BatchSummary,
}
