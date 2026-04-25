// rust/ssot_pipeline/src/audit.rs
//
// Route envelope auditor for the Dual Punch SSOT pipeline.

use crate::types::RouteAuditResult;

/// Audit a planned 2-leg route envelope against canonical constant-product invariants.
///
/// Invariants checked:
/// 1. `B_in_2 == B_out_1` — inventory handoff with no slippage subtraction.
/// 2. `P_gross == A_out_2 − A_in` — profit after returning to starting asset.
/// 3. `P_net == P_gross − C_total` — net profit accounts for all costs.
/// 4. `fee1 ∈ [0, 1)` and `fee2 ∈ [0, 1)` — fee rates in valid range.
pub fn audit_two_leg_route_envelope(
    a_in: f64,
    fee1: f64,
    b_out_1: f64,
    b_in_2: f64,
    fee2: f64,
    a_out_2: f64,
    p_gross: f64,
    p_net: f64,
    c_total: f64,
    tolerance: f64,
) -> RouteAuditResult {
    let mut violations: Vec<String> = Vec::new();

    // 1. Inventory handoff
    if (b_in_2 - b_out_1).abs() > tolerance {
        violations.push(format!(
            "inventory_drift: b_in_2={:.10} != b_out_1={:.10} (delta={:.2e})",
            b_in_2,
            b_out_1,
            b_in_2 - b_out_1
        ));
    }

    // 2. Gross profit identity
    let expected_p_gross = a_out_2 - a_in;
    if (p_gross - expected_p_gross).abs() > tolerance {
        violations.push(format!(
            "p_gross_mismatch: declared={:.10}, expected A_out_2 - A_in={:.10} (delta={:.2e})",
            p_gross,
            expected_p_gross,
            p_gross - expected_p_gross
        ));
    }

    // 3. Net profit identity
    let expected_p_net = p_gross - c_total;
    if (p_net - expected_p_net).abs() > tolerance {
        violations.push(format!(
            "p_net_mismatch: declared={:.10}, expected P_gross - C_total={:.10} (delta={:.2e})",
            p_net,
            expected_p_net,
            p_net - expected_p_net
        ));
    }

    // 4. Fee range checks
    if fee1 < 0.0 || fee1 >= 1.0 {
        violations.push(format!("fee1_range: fee1={fee1} is outside [0, 1)"));
    }
    if fee2 < 0.0 || fee2 >= 1.0 {
        violations.push(format!("fee2_range: fee2={fee2} is outside [0, 1)"));
    }

    RouteAuditResult {
        passed: violations.is_empty(),
        violations,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_audit() -> RouteAuditResult {
        let a_in = 1000.0_f64;
        let b_out_1 = 995.0_f64;
        let a_out_2 = 1008.5_f64;
        let p_gross = a_out_2 - a_in;
        let c_total = 1.0_f64;
        let p_net = p_gross - c_total;
        audit_two_leg_route_envelope(a_in, 0.003, b_out_1, b_out_1, 0.0025, a_out_2, p_gross, p_net, c_total, 1e-9)
    }

    #[test]
    fn valid_plan_passes() {
        let r = valid_audit();
        assert!(r.passed);
        assert!(r.violations.is_empty());
    }

    #[test]
    fn inventory_drift_detected() {
        let a_in = 1000.0_f64;
        let b_out_1 = 995.0_f64;
        let a_out_2 = 1008.5_f64;
        let p_gross = a_out_2 - a_in;
        let c_total = 1.0_f64;
        let p_net = p_gross - c_total;
        let r = audit_two_leg_route_envelope(a_in, 0.003, b_out_1, 900.0, 0.0025, a_out_2, p_gross, p_net, c_total, 1e-9);
        assert!(!r.passed);
        assert!(r.violations.iter().any(|v| v.contains("inventory_drift")));
    }

    #[test]
    fn p_gross_mismatch_detected() {
        let a_in = 1000.0_f64;
        let b_out_1 = 995.0_f64;
        let a_out_2 = 1008.5_f64;
        let p_gross = a_out_2 - a_in;
        let c_total = 1.0_f64;
        let p_net = p_gross - c_total;
        let r = audit_two_leg_route_envelope(a_in, 0.003, b_out_1, b_out_1, 0.0025, a_out_2, 9999.0, p_net, c_total, 1e-9);
        assert!(!r.passed);
        assert!(r.violations.iter().any(|v| v.contains("p_gross_mismatch")));
    }

    #[test]
    fn fee1_out_of_range_detected() {
        let a_in = 1000.0_f64;
        let b_out_1 = 995.0_f64;
        let a_out_2 = 1008.5_f64;
        let p_gross = a_out_2 - a_in;
        let c_total = 1.0_f64;
        let p_net = p_gross - c_total;
        let r = audit_two_leg_route_envelope(a_in, -0.001, b_out_1, b_out_1, 0.0025, a_out_2, p_gross, p_net, c_total, 1e-9);
        assert!(!r.passed);
        assert!(r.violations.iter().any(|v| v.contains("fee1_range")));
    }

    #[test]
    fn fee2_at_one_detected() {
        let a_in = 1000.0_f64;
        let b_out_1 = 995.0_f64;
        let a_out_2 = 1008.5_f64;
        let p_gross = a_out_2 - a_in;
        let c_total = 1.0_f64;
        let p_net = p_gross - c_total;
        let r = audit_two_leg_route_envelope(a_in, 0.003, b_out_1, b_out_1, 1.0, a_out_2, p_gross, p_net, c_total, 1e-9);
        assert!(!r.passed);
        assert!(r.violations.iter().any(|v| v.contains("fee2_range")));
    }
}
