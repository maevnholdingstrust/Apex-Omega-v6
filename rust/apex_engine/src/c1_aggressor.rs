use crate::evm_mirror::MirrorError;
use crate::types::{RouteCandidate, RouteEnvelope, RouteStep};

pub fn build_c1_envelope_from_steps(
    profit_token: [u8; 20],
    steps: Vec<RouteStep>,
) -> Result<RouteEnvelope, MirrorError> {
    if steps.is_empty() {
        return Err(MirrorError::EmptyRoute);
    }
    if steps.iter().any(|step| step.data.is_empty()) {
        return Err(MirrorError::EmptyCalldata);
    }
    Ok(RouteEnvelope {
        version: 1,
        profit_token,
        gas_reserve_asset: 0,
        dex_fee_reserve_asset: 0,
        steps,
    })
}

pub fn candidate_is_ev_positive(candidate: &RouteCandidate, min_ev_usd: f64) -> bool {
    candidate.expected_ev_usd > min_ev_usd
}
