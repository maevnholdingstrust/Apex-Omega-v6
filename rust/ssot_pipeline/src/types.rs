use std::collections::HashMap;

pub type Address = [u8; 20];
pub type Bytes = Vec<u8>;
pub type B256 = [u8; 32];

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PoolFamily {
    V2Cpmm,
    V3Clmm,
    AlgebraClmm,
    CurveStable,
    BalancerWeighted,
    Unknown,
}

#[derive(Clone, Debug)]
pub struct TokenAmount {
    pub token: Address,
    pub amount: u128,
}

#[derive(Clone, Debug)]
pub struct PoolState {
    pub address: Address,
    pub token0: Address,
    pub token1: Address,
    pub reserve0: u128,
    pub reserve1: u128,
    pub fee_bps: u16,
    pub family: PoolFamily,
    pub block_number: u64,
    pub verified: bool,
    pub tvl_usd: f64,
}

#[derive(Clone, Debug)]
pub struct RouteLeg {
    pub pool: Address,
    pub token_in: Address,
    pub token_out: Address,
    pub amount_in: u128,
    pub expected_out: u128,
    pub min_out: u128,
    pub fee_bps: u16,
    pub family: PoolFamily,
}

#[derive(Clone, Debug)]
pub struct RouteCandidate {
    pub route_id: String,
    pub legs: Vec<RouteLeg>,
    pub amount_in: u128,
    pub expected_final_out: u128,
    pub min_final_out: u128,
    pub raw_spread_decimal: f64,
    pub expected_profit_usd: f64,
    pub expected_ev_usd: f64,
}

#[derive(Clone, Debug)]
pub struct RouteStep {
    pub protocol: u8,
    pub target: Address,
    pub approve_token: Address,
    pub output_token: Address,
    pub call_value: u128,
    pub min_amount_in: u128,
    pub min_amount_out: u128,
    pub fee_bps: u16,
    pub data: Bytes,
    pub optional: bool,
}

#[derive(Clone, Debug)]
pub struct RouteEnvelope {
    pub version: u8,
    pub profit_token: Address,
    pub gas_reserve_asset: u128,
    pub dex_fee_reserve_asset: u128,
    pub steps: Vec<RouteStep>,
}

#[derive(Clone, Debug)]
pub enum C2Action {
    Mirror,
    Reverse,
    DoNothing,
}

#[derive(Clone, Debug)]
pub struct C2Candidate {
    pub action: C2Action,
    pub route: Option<RouteEnvelope>,
    pub selected_buffer: f64,
    pub expected_ev_usd: f64,
    pub merkle_leaf: Option<B256>,
}

#[derive(Clone, Debug)]
pub struct OpportunityCycle {
    pub cycle_id: u64,
    pub chain_id: u64,
    pub token: Address,
    pub c1_block_target: u64,
    pub c2_block_target: u64,
    pub c1_expected_ev_usd: f64,
    pub c2_expected_ev_usd: f64,
    pub c2_action: Option<C2Action>,
}

#[derive(Clone, Debug)]
pub struct TokenState {
    pub balances: HashMap<Address, u128>,
    pub allowances: HashMap<(Address, Address), u128>,
}

#[derive(Clone, Debug, Default)]
pub struct GlobalState {
    pub tokens: HashMap<Address, TokenState>,
    pub pools: HashMap<Address, PoolState>,
    pub block_number: u64,
}
