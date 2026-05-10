use crate::types::{Address, GlobalState, RouteEnvelope, RouteStep};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MirrorError {
    EmptyRoute,
    EmptyCalldata,
    InsufficientAllowance,
    InsufficientBalance,
    DispatchHookNotWired,
    MinOutNotMet,
    ProfitBelowMinimum,
}

impl GlobalState {
    pub fn balance_of(&self, owner: Address, token: Address) -> u128 {
        *self.balances.get(&(owner, token)).unwrap_or(&0)
    }

    pub fn allowance(&self, owner: Address, spender: Address, token: Address) -> u128 {
        *self.allowances.get(&(owner, spender, token)).unwrap_or(&0)
    }

    pub fn set_balance(&mut self, owner: Address, token: Address, amount: u128) {
        self.balances.insert((owner, token), amount);
    }

    pub fn set_allowance(
        &mut self,
        owner: Address,
        spender: Address,
        token: Address,
        amount: u128,
    ) {
        self.allowances.insert((owner, spender, token), amount);
    }
}

pub fn simulate_target_call(
    _state: &GlobalState,
    step: &RouteStep,
    amount_in: u128,
) -> Result<u128, MirrorError> {
    if step.data.is_empty() {
        return Err(MirrorError::EmptyCalldata);
    }
    if step.protocol == 2 {
        return Ok(amount_in.saturating_sub(step.fee_bps as u128));
    }
    Err(MirrorError::DispatchHookNotWired)
}

pub fn mirror_c1_execute_envelope(
    state: &GlobalState,
    owner: Address,
    envelope: &RouteEnvelope,
) -> Result<u128, MirrorError> {
    validate_envelope(envelope)?;
    let mut amount = envelope.steps[0].min_amount_in;
    for step in &envelope.steps {
        if state.allowance(owner, step.target, step.approve_token) < step.min_amount_in {
            return Err(MirrorError::InsufficientAllowance);
        }
        if state.balance_of(owner, step.approve_token) < step.min_amount_in {
            return Err(MirrorError::InsufficientBalance);
        }
        amount = simulate_target_call(state, step, amount)?;
        if amount < step.min_amount_out {
            return Err(MirrorError::MinOutNotMet);
        }
    }
    Ok(amount)
}

pub fn mirror_c2_execute_route_and_settle(
    state: &GlobalState,
    owner: Address,
    envelope: &RouteEnvelope,
    flash_repayment: u128,
    min_profit: u128,
) -> Result<u128, MirrorError> {
    let final_out = mirror_c1_execute_envelope(state, owner, envelope)?;
    let required = flash_repayment.saturating_add(min_profit);
    if final_out < required {
        return Err(MirrorError::ProfitBelowMinimum);
    }
    Ok(final_out - flash_repayment)
}

fn validate_envelope(envelope: &RouteEnvelope) -> Result<(), MirrorError> {
    if envelope.steps.is_empty() {
        return Err(MirrorError::EmptyRoute);
    }
    if envelope.steps.iter().any(|step| step.data.is_empty()) {
        return Err(MirrorError::EmptyCalldata);
    }
    Ok(())
}
