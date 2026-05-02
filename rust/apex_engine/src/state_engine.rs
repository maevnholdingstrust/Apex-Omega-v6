use crate::types::{Address, GlobalState, PoolFamily};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PoolGateError {
    MissingPool,
    UnverifiedPool,
    ZeroReserves,
    StalePool,
    DustTvl,
    UnsupportedPoolFamily,
}

pub fn validate_pool_for_execution(
    state: &GlobalState,
    address: Address,
    min_tvl_usd: f64,
) -> Result<(), PoolGateError> {
    let pool = state
        .pools
        .get(&address)
        .ok_or(PoolGateError::MissingPool)?;
    if !pool.verified {
        return Err(PoolGateError::UnverifiedPool);
    }
    if pool.reserve0 == 0 || pool.reserve1 == 0 {
        return Err(PoolGateError::ZeroReserves);
    }
    if pool.block_number == 0 || pool.block_number < state.block_number.saturating_sub(5) {
        return Err(PoolGateError::StalePool);
    }
    if pool.tvl_usd < min_tvl_usd {
        return Err(PoolGateError::DustTvl);
    }
    if pool.family != PoolFamily::V2_CPMM {
        return Err(PoolGateError::UnsupportedPoolFamily);
    }
    Ok(())
}
