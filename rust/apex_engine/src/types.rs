use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub type Address = [u8; 20];
pub type Bytes = Vec<u8>;
pub type B256 = [u8; 32];

#[allow(non_camel_case_types)]
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PoolFamily {
    V2_CPMM,
    V3_CLMM,
    ALGEBRA_CLMM,
    CURVE_STABLE,
    BALANCER_WEIGHTED,
    UNKNOWN,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteEnvelope {
    pub version: u8,
    pub profit_token: Address,
    pub gas_reserve_asset: u128,
    pub dex_fee_reserve_asset: u128,
    pub steps: Vec<RouteStep>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum C2Action {
    Mirror,
    Reverse,
    DoNothing,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct C2Candidate {
    pub action: C2Action,
    pub route: Option<RouteEnvelope>,
    pub selected_buffer: f64,
    pub expected_ev_usd: f64,
    pub merkle_leaf: Option<B256>,
    pub executable: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct GlobalState {
    pub pools: HashMap<Address, PoolState>,
    pub balances: HashMap<(Address, Address), u128>,
    pub allowances: HashMap<(Address, Address, Address), u128>,
    pub block_number: u64,
}
