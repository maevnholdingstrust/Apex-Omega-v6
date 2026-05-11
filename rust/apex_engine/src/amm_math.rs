use crate::types::{PoolFamily, PoolState};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AmmError {
    UnsupportedPoolFamily,
    ZeroReserve,
    MathOverflow,
    InvalidRoute,
}

pub fn quote_exact_in(
    pool: &PoolState,
    token_in_is_token0: bool,
    amount_in: u128,
) -> Result<u128, AmmError> {
    if pool.family != PoolFamily::V2_CPMM {
        return Err(AmmError::UnsupportedPoolFamily);
    }
    let (reserve_in, reserve_out) = if token_in_is_token0 {
        (pool.reserve0, pool.reserve1)
    } else {
        (pool.reserve1, pool.reserve0)
    };
    if reserve_in == 0 || reserve_out == 0 {
        return Err(AmmError::ZeroReserve);
    }
    let fee_den = 10_000u128;
    let fee_num = fee_den
        .checked_sub(pool.fee_bps as u128)
        .ok_or(AmmError::MathOverflow)?;
    let amount_in_after_fee = amount_in
        .checked_mul(fee_num)
        .ok_or(AmmError::MathOverflow)?
        / fee_den;
    let numerator = amount_in_after_fee
        .checked_mul(reserve_out)
        .ok_or(AmmError::MathOverflow)?;
    let denominator = reserve_in
        .checked_add(amount_in_after_fee)
        .ok_or(AmmError::MathOverflow)?;
    Ok(numerator / denominator)
}

pub fn simulate_two_leg(
    leg1: &PoolState,
    leg1_token_in_is_token0: bool,
    leg2: &PoolState,
    leg2_token_in_is_token0: bool,
    amount_in: u128,
) -> Result<(u128, u128), AmmError> {
    let leg1_out = quote_exact_in(leg1, leg1_token_in_is_token0, amount_in)?;
    let leg2_out = quote_exact_in(leg2, leg2_token_in_is_token0, leg1_out)?;
    Ok((leg1_out, leg2_out))
}
