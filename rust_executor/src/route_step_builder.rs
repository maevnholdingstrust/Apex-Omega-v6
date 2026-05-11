use ethers::types::{Address, Bytes, U256};

#[derive(Clone, Debug)]
pub struct RouteStep {
    pub protocol: u8,
    pub target: Address,
    pub approve_token: Address,
    pub output_token: Address,
    pub call_value: U256,
    pub min_amount_in: U256,
    pub min_amount_out: U256,
    pub fee_bps: u16,
    pub data: Bytes,
}

pub fn build_route_step(
    protocol: u8,
    target: Address,
    approve_token: Address,
    output_token: Address,
    amount_in: U256,
    min_amount_out: U256,
    fee_bps: u16,
    data: Bytes,
) -> anyhow::Result<RouteStep> {
    if data.0.len() < 4 {
        anyhow::bail!("RouteStep rejected: missing router calldata");
    }

    if target == Address::zero() {
        anyhow::bail!("RouteStep rejected: target is zero");
    }

    if approve_token == Address::zero() {
        anyhow::bail!("RouteStep rejected: approve_token is zero");
    }

    if output_token == Address::zero() {
        anyhow::bail!("RouteStep rejected: output_token is zero");
    }

    if amount_in.is_zero() || min_amount_out.is_zero() {
        anyhow::bail!("RouteStep rejected: zero amount");
    }

    Ok(RouteStep {
        protocol,
        target,
        approve_token,
        output_token,
        call_value: U256::zero(),
        min_amount_in: amount_in,
        min_amount_out,
        fee_bps,
        data,
    })
}

pub fn assert_live_ready(steps: &[RouteStep]) -> anyhow::Result<()> {
    if steps.is_empty() {
        anyhow::bail!("live blocked: empty route steps");
    }

    for step in steps {
        if step.data.0.len() < 4 {
            anyhow::bail!("live blocked: empty RouteStep.data");
        }

        if step.min_amount_in.is_zero() {
            anyhow::bail!("live blocked: min_amount_in is zero");
        }

        if step.min_amount_out.is_zero() {
            anyhow::bail!("live blocked: min_amount_out is zero");
        }
    }

    Ok(())
}
