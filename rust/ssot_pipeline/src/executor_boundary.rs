// rust/ssot_pipeline/src/executor_boundary.rs
//
// Executor boundary for the Dual Punch SSOT pipeline (Phase 1).
//
// This module exposes the explicit integration point between the SSOT pipeline
// and the downstream executor/payload builder (Phase 2+).  Only objects that
// have passed the full SSOT pipeline — math, audit, and C2 gate — may cross
// this boundary.
//
// Execution rule: no object reaches calldata/bundle construction unless it
// passed the SSOT audit AND C2 returned STRIKE.
//
// Router calldata, ABI encoding, private relay submission, and on-chain
// execution are NOT implemented here; they belong in a separate Phase 2 crate.

use crate::types::PipelineFinalResult;

/// Approved execution plan ready to be handed off to the payload builder.
///
/// This type is the *only* legal input to the executor pipeline.  It is
/// produced by extracting a successfully-audited STRIKE result from a
/// `PipelineFinalResult`; all other pipeline outcomes are rejected at this
/// boundary.
#[derive(Clone, Debug)]
pub struct ApprovedExecutionPlan {
    pub best_size: f64,
    pub p_net_deterministic: f64,
    pub ev: f64,
}

/// Extract an `ApprovedExecutionPlan` from a completed `PipelineFinalResult`.
///
/// # Errors
///
/// Returns `Err` when:
/// - The route audit did not pass (`audit.passed == false`).
/// - C2 decided `DO_NOTHING` (not `STRIKE`).
///
/// The caller must not attempt to route around these guards.
pub fn extract_approved_plan(result: &PipelineFinalResult) -> Result<ApprovedExecutionPlan, String> {
    if !result.audit.passed {
        return Err(format!(
            "execution rejected: audit failed with {} violation(s): {:?}",
            result.audit.violations.len(),
            result.audit.violations
        ));
    }

    if result.c2_decision != "STRIKE" {
        return Err(format!(
            "execution rejected: C2 decision is {:?}, not STRIKE",
            result.c2_decision
        ));
    }

    Ok(ApprovedExecutionPlan {
        best_size: result.best_size,
        p_net_deterministic: result.p_net_deterministic,
        ev: result.ev,
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{BatchSummary, RouteAuditResult};

    fn make_result(passed: bool, c2: &str) -> PipelineFinalResult {
        PipelineFinalResult {
            best_size: 1000.0,
            p_net_deterministic: 10.0,
            ev: 10.0,
            c2_decision: c2.to_string(),
            audit: RouteAuditResult { passed, violations: vec![] },
            batch_summary: BatchSummary {
                n_runs: 1,
                n_strikes: 1,
                n_profitable_strikes: 1,
                total_actual_profit: 6.5,
                mean_actual_profit_per_run: 6.5,
                hit_rate: 1.0,
                ev: 10.0,
            },
        }
    }

    #[test]
    fn approved_plan_extracted_on_strike_and_passed_audit() {
        let result = make_result(true, "STRIKE");
        let plan = extract_approved_plan(&result).unwrap();
        assert_eq!(plan.best_size, 1000.0);
        assert_eq!(plan.p_net_deterministic, 10.0);
    }

    #[test]
    fn rejected_when_audit_failed() {
        let result = make_result(false, "STRIKE");
        assert!(extract_approved_plan(&result).is_err());
    }

    #[test]
    fn rejected_when_c2_do_nothing() {
        let result = make_result(true, "DO_NOTHING");
        let err = extract_approved_plan(&result).unwrap_err();
        assert!(err.contains("DO_NOTHING"));
    }

    #[test]
    fn rejected_when_both_fail() {
        let result = make_result(false, "DO_NOTHING");
        assert!(extract_approved_plan(&result).is_err());
    }
}
