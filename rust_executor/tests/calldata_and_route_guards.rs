use ethers::types::{Address, Bytes, U256};
use ethers::utils::id;
use rust_executor::calldata::{build_router_calldata, RouterKind, SwapCallInput};
use rust_executor::route_envelope::RouteEnvelope;
use rust_executor::route_step_builder::{assert_live_ready, build_route_step};

fn addr(byte: u8) -> Address {
    Address::from_slice(&[byte; 20])
}

fn swap_input() -> SwapCallInput {
    SwapCallInput {
        router: addr(0x11),
        token_in: addr(0x22),
        token_out: addr(0x33),
        recipient: addr(0x44),
        amount_in: U256::from(1_000_u64),
        min_amount_out: U256::from(990_u64),
        fee: Some(500),
        deadline: U256::from(1_900_000_000_u64),
        raw_aggregator_data: None,
    }
}

#[test]
fn v2_generator_builds_swap_exact_tokens_selector() {
    let data = build_router_calldata(RouterKind::QuickSwapV2, &swap_input()).unwrap();
    let selector = &id("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)")[0..4];

    assert_eq!(&data.0[0..4], selector);
    assert!(data.0.len() > 4);
}

#[test]
fn v3_generator_requires_fee_and_builds_exact_input_single_selector() {
    let input = swap_input();
    let data = build_router_calldata(RouterKind::UniswapV3, &input).unwrap();
    let selector =
        &id("exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))")
            [0..4];

    assert_eq!(&data.0[0..4], selector);

    let mut missing_fee = input;
    missing_fee.fee = None;
    assert!(build_router_calldata(RouterKind::UniswapV3, &missing_fee).is_err());
}

#[test]
fn aggregator_generator_rejects_missing_selector() {
    let mut input = swap_input();
    input.raw_aggregator_data = Some(Bytes::from(vec![0xab, 0xcd]));

    assert!(build_router_calldata(RouterKind::Odos, &input).is_err());

    input.raw_aggregator_data = Some(Bytes::from(vec![0xab, 0xcd, 0xef, 0x01]));
    assert!(build_router_calldata(RouterKind::ZeroX, &input).is_ok());
}

#[test]
fn route_step_and_live_guard_reject_empty_calldata() {
    let err = build_route_step(
        1,
        addr(0x11),
        addr(0x22),
        addr(0x33),
        U256::from(1_000_u64),
        U256::from(990_u64),
        30,
        Bytes::from(vec![0x00]),
    )
    .unwrap_err();
    assert!(err.to_string().contains("missing router calldata"));
}

#[test]
fn route_envelope_requires_live_ready_steps() {
    let step = build_route_step(
        1,
        addr(0x11),
        addr(0x22),
        addr(0x33),
        U256::from(1_000_u64),
        U256::from(990_u64),
        30,
        Bytes::from(vec![0x12, 0x34, 0x56, 0x78]),
    )
    .unwrap();

    assert!(assert_live_ready(&[step.clone()]).is_ok());

    let envelope =
        RouteEnvelope::new(1, addr(0x44), U256::zero(), U256::zero(), vec![step]).unwrap();
    assert!(!envelope.abi_encode_institutional().unwrap().0.is_empty());
}
