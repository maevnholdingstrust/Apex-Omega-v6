// rust/ssot_pipeline/src/math_core.rs
//
// Standalone constant-product AMM math for the Dual Punch SSOT pipeline.
//
// The AMM formula and 5-phase model are identical to those in the Python
// ssot_pipeline.math_core module (and the apex_omega_core Rust extension).

/// Constant-product AMM swap with fee; slippage is embedded in the output.
///
/// Returns `0.0` when any input is non-positive.
pub fn amm_swap(amount_in: f64, reserve_in: f64, reserve_out: f64, fee: f64) -> f64 {
    let amount_in_with_fee = amount_in * (1.0 - fee);
    if reserve_in <= 0.0 || reserve_out <= 0.0 || amount_in_with_fee <= 0.0 {
        return 0.0;
    }
    (amount_in_with_fee * reserve_out) / (reserve_in + amount_in_with_fee)
}

/// Output of the two-leg arbitrage profit computation.
#[derive(Clone, Debug)]
pub struct TwoLegArbResult {
    /// Swap 1 output (asset B); becomes Swap 2 input.
    pub b_out_1: f64,
    /// Swap 2 output (asset A); final inventory.
    pub a_out_2: f64,
    /// Gross profit in asset A = a_out_2 − a_in.
    pub p_gross: f64,
    /// Net profit  in asset A = p_gross − c_gas − c_loan − c_other.
    pub p_net: f64,
}

/// Canonical two-swap arbitrage profit using constant-product AMM math.
///
/// Implements the spec-locked 5-phase two-swap form:
///
/// Phase A — start with `a_in` units of asset A.
/// Phase B — Swap 1 (A → B): A_eff = a_in × (1 − fee1); B_out_1 = (A_eff × r1_out) / (r1_in + A_eff)
/// Phase C — inventory handoff: b_out_1 feeds directly into Swap 2.
/// Phase D — Swap 2 (B → A): B_eff = b_out_1 × (1 − fee2); a_out_2 = (B_eff × r2_out) / (r2_in + B_eff)
/// Phase E — p_gross = a_out_2 − a_in; p_net = p_gross − c_gas − c_loan − c_other
pub fn two_leg_arb_profit(
    a_in: f64,
    fee1: f64,
    r1_in: f64,
    r1_out: f64,
    fee2: f64,
    r2_in: f64,
    r2_out: f64,
    c_gas: f64,
    c_loan: f64,
    c_other: f64,
) -> TwoLegArbResult {
    let b_out_1 = amm_swap(a_in, r1_in, r1_out, fee1);
    let a_out_2 = amm_swap(b_out_1, r2_in, r2_out, fee2);
    let p_gross = a_out_2 - a_in;
    let p_net = p_gross - c_gas - c_loan - c_other;
    TwoLegArbResult { b_out_1, a_out_2, p_gross, p_net }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn amm_swap_basic() {
        let out = amm_swap(100.0, 10_000.0, 10_000.0, 0.003);
        let expected = 99.7 * 10_000.0 / (10_000.0 + 99.7);
        assert!((out - expected).abs() < 1e-9, "out={out} expected={expected}");
    }

    #[test]
    fn amm_swap_zero_reserve_in() {
        assert_eq!(amm_swap(100.0, 0.0, 10_000.0, 0.003), 0.0);
    }

    #[test]
    fn amm_swap_zero_amount_in() {
        assert_eq!(amm_swap(0.0, 10_000.0, 10_000.0, 0.003), 0.0);
    }

    #[test]
    fn amm_swap_full_fee_returns_zero() {
        assert_eq!(amm_swap(100.0, 10_000.0, 10_000.0, 1.0), 0.0);
    }

    #[test]
    fn two_leg_symmetric_pool_produces_loss() {
        let r = two_leg_arb_profit(1000.0, 0.003, 100_000.0, 100_000.0, 0.003, 100_000.0, 100_000.0, 0.0, 0.0, 0.0);
        assert!(r.p_gross < 0.0);
    }

    #[test]
    fn two_leg_profitable_arb() {
        let r = two_leg_arb_profit(100.0, 0.003, 1_000.0, 2_000.0, 0.003, 2_000.0, 1_500.0, 0.0, 0.0, 0.0);
        assert!(r.p_gross > 0.0);
    }

    #[test]
    fn two_leg_cost_reduces_p_net() {
        let base = two_leg_arb_profit(100.0, 0.003, 1_000.0, 2_000.0, 0.003, 2_000.0, 1_500.0, 0.0, 0.0, 0.0);
        let with_cost = two_leg_arb_profit(100.0, 0.003, 1_000.0, 2_000.0, 0.003, 2_000.0, 1_500.0, 5.0, 0.0, 0.0);
        assert!((with_cost.p_net - (base.p_net - 5.0)).abs() < 1e-12);
    }

    #[test]
    fn p_gross_identity() {
        let r = two_leg_arb_profit(500.0, 0.002, 50_000.0, 55_000.0, 0.002, 55_000.0, 50_000.0, 0.0, 0.0, 0.0);
        assert!((r.p_gross - (r.a_out_2 - 500.0)).abs() < 1e-12);
    }
}
