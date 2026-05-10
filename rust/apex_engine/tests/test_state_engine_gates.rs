use apex_engine::{validate_pool_for_execution, GlobalState, PoolFamily, PoolGateError, PoolState};

fn addr(v: u8) -> [u8; 20] {
    [v; 20]
}

fn state_with(pool: PoolState) -> GlobalState {
    let mut state = GlobalState {
        block_number: 100,
        ..GlobalState::default()
    };
    state.pools.insert(pool.address, pool);
    state
}

fn valid_pool() -> PoolState {
    PoolState {
        address: addr(1),
        token0: addr(2),
        token1: addr(3),
        reserve0: 1_000_000,
        reserve1: 2_000_000,
        fee_bps: 30,
        family: PoolFamily::V2_CPMM,
        block_number: 100,
        verified: true,
        tvl_usd: 100_000.0,
    }
}

#[test]
fn missing_pool_rejected() {
    let state = GlobalState {
        block_number: 100,
        ..GlobalState::default()
    };
    assert_eq!(
        validate_pool_for_execution(&state, addr(1), 10_000.0),
        Err(PoolGateError::MissingPool)
    );
}

#[test]
fn unverified_pool_rejected() {
    let mut pool = valid_pool();
    pool.verified = false;
    assert_eq!(
        validate_pool_for_execution(&state_with(pool), addr(1), 10_000.0),
        Err(PoolGateError::UnverifiedPool)
    );
}

#[test]
fn zero_reserves_rejected() {
    let mut pool = valid_pool();
    pool.reserve0 = 0;
    assert_eq!(
        validate_pool_for_execution(&state_with(pool), addr(1), 10_000.0),
        Err(PoolGateError::ZeroReserves)
    );
}

#[test]
fn stale_pool_rejected() {
    let mut pool = valid_pool();
    pool.block_number = 90;
    assert_eq!(
        validate_pool_for_execution(&state_with(pool), addr(1), 10_000.0),
        Err(PoolGateError::StalePool)
    );
}

#[test]
fn dust_tvl_rejected() {
    let mut pool = valid_pool();
    pool.tvl_usd = 9_999.0;
    assert_eq!(
        validate_pool_for_execution(&state_with(pool), addr(1), 10_000.0),
        Err(PoolGateError::DustTvl)
    );
}

#[test]
fn v3_rejected_for_execution() {
    let mut pool = valid_pool();
    pool.family = PoolFamily::V3_CLMM;
    assert_eq!(
        validate_pool_for_execution(&state_with(pool), addr(1), 10_000.0),
        Err(PoolGateError::UnsupportedPoolFamily)
    );
}

#[test]
fn valid_verified_v2_pool_passes() {
    assert_eq!(
        validate_pool_for_execution(&state_with(valid_pool()), addr(1), 10_000.0),
        Ok(())
    );
}
