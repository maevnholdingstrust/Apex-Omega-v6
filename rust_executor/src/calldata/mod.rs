use ethers::types::{Address, Bytes, U256};

pub mod aggregator;
pub mod algebra;
pub mod v2;
pub mod v3;

#[derive(Clone, Debug)]
pub enum RouterKind {
    QuickSwapV2,
    UniswapV2Like,
    UniswapV3,
    Algebra,
    Odos,
    ZeroX,
}

#[derive(Clone, Debug)]
pub struct SwapCallInput {
    pub router: Address,
    pub token_in: Address,
    pub token_out: Address,
    pub recipient: Address,
    pub amount_in: U256,
    pub min_amount_out: U256,
    pub fee: Option<u32>,
    pub deadline: U256,
    pub raw_aggregator_data: Option<Bytes>,
}

pub trait CalldataGenerator {
    fn build_swap_calldata(input: &SwapCallInput) -> anyhow::Result<Bytes>;
}

pub fn build_router_calldata(kind: RouterKind, input: &SwapCallInput) -> anyhow::Result<Bytes> {
    match kind {
        RouterKind::QuickSwapV2 | RouterKind::UniswapV2Like => {
            v2::V2Calldata::build_swap_calldata(input)
        }
        RouterKind::UniswapV3 => v3::V3Calldata::build_swap_calldata(input),
        RouterKind::Algebra => algebra::AlgebraCalldata::build_swap_calldata(input),
        RouterKind::Odos | RouterKind::ZeroX => {
            aggregator::AggregatorCalldata::build_swap_calldata(input)
        }
    }
}
