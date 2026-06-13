import pytest
from eth_abi import decode
from web3 import Web3

from apex_omega_core.core.execution_engine import ExecutionEngine
from apex_omega_core.core.runtime_config import RuntimeConfig


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        chain_id=137,
        environment="test",
        live_trading_enabled=False,
        dry_run=True,
        polygon_rpc="https://polygon-rpc.com/",
        polygon_wss="",
        polygon_private_mempool_rpc_url="",
        executor_private_key="",
        bundle_signer_private_key="",
        c1_executor_address="0x1111111111111111111111111111111111111111",
        c2_executor_address="0x2222222222222222222222222222222222222222",
        liquidation_executor_address="0x3333333333333333333333333333333333333333",
        aave_v3_pool_address="0x3333333333333333333333333333333333333333",
        balancer_vault_address="0x4444444444444444444444444444444444444444",
        titan_mev_us_west="",
        flashbots_relay="",
        fastlane_relay="",
        marlin_relay="",
        min_net_profit_usd=1.0,
        min_raw_spread_bps=1.0,
        max_route_slippage_bps=100.0,
        max_mempool_degradation_bps=200.0,
        min_pool_tvl_usd=10_000.0,
        max_trade_to_pool_ratio_bps=500.0,
        risk_buffer_usd=0.0,
        c1_gas_usd=0.38,
        c2_gas_usd=0.55,
        flash_loan_fee_bps=9.0,
        bundle_target_block_offset=1,
        bundle_max_block_window=5,
    )


def _step(token_in: str, token_out: str, amount_in: int, min_out: int) -> dict:
    return {
        "protocol": 1,
        "target": "0x5555555555555555555555555555555555555555",
        "approveToken": token_in,
        "outputToken": token_out,
        "callValue": 0,
        "minAmountIn": amount_in,
        "minAmountOut": min_out,
        "feeBps": 30,
        "data": b"\x12\x34\x56\x78" + amount_in.to_bytes(32, "big"),
    }


def _strategy(receiver: str = "0x1111111111111111111111111111111111111111") -> dict:
    usdc = "0x7777777777777777777777777777777777777777"
    mid = "0x8888888888888888888888888888888888888888"
    return {
        "asset": usdc,
        "executor_address": receiver,
        "flash_loan_receiver": receiver,
        "min_profit": 42,
        "flash_loan_amount": 1_000_000,
        "steps": [
            _step(usdc, mid, 1_000_000, 990_000),
            _step(mid, usdc, 990_000, 1_000_042),
        ],
    }


def test_c1_plan_wraps_route_envelope_in_flashloan_entrypoint():
    engine = ExecutionEngine(_config())

    plan = engine.build_c1_plan(_strategy())

    expected_selector = Web3.keccak(
        text="initAaveFlash(address,uint256,uint256,bytes)"
    )[:4]
    assert plan.calldata[:4] == expected_selector
    decoded = decode(["address", "uint256", "uint256", "bytes"], plan.calldata[4:])
    assert decoded[0] == Web3.to_checksum_address(_strategy()["asset"])
    assert decoded[1] == 1_000_000
    assert decoded[2] == 42
    assert decoded[3] == plan.compiled.encoded_payload


def test_c1_plan_rejects_missing_flashloan_receiver():
    engine = ExecutionEngine(_config())
    strategy = _strategy()
    strategy.pop("executor_address")
    strategy.pop("flash_loan_receiver")

    with pytest.raises(ValueError, match="requires flash_loan_receiver"):
        engine.build_c1_plan(strategy)


def test_c1_plan_rejects_receiver_that_is_not_c1_target():
    engine = ExecutionEngine(_config())

    with pytest.raises(ValueError, match="receiver must equal configured C1"):
        engine.build_c1_plan(_strategy("0x9999999999999999999999999999999999999999"))


def test_c1_plan_rejects_executor_field_that_disagrees_with_c1_target():
    engine = ExecutionEngine(_config())
    strategy = _strategy()
    strategy["executor_address"] = "0x9999999999999999999999999999999999999999"

    with pytest.raises(ValueError, match="receiver must equal configured C1"):
        engine.build_c1_plan(strategy)


def test_c2_plan_wraps_route_envelope_in_execute_arbitrage_with_merkle_leaf():
    engine = ExecutionEngine(_config())

    plan = engine.build_c2_plan(_strategy())

    expected_selector = Web3.keccak(
        text="executeArbitrage(address,uint256,uint256,bytes32[],bytes)"
    )[:4]
    assert plan.calldata[:4] == expected_selector
    decoded = decode(["address", "uint256", "uint256", "bytes32[]", "bytes"], plan.calldata[4:])
    assert decoded[0] == Web3.to_checksum_address(_strategy()["asset"])
    assert decoded[1] == 1_000_000
    assert decoded[2] == 42
    assert decoded[3] == ()
    assert decoded[4] == plan.compiled.encoded_payload
    assert plan.merkle_leaf == Web3.keccak(plan.compiled.encoded_payload)


def test_c2_ultimate_envelope_uses_contract_level_guards_only_where_measurable():
    engine = ExecutionEngine(_config())

    plan = engine.build_c2_plan(_strategy())
    decoded_route = decode(
        [
            "(uint8,address,uint256,uint256,(uint8,address,address,uint256,uint256,uint256,uint16,bytes)[])",
        ],
        plan.compiled.encoded_payload,
    )[0]
    steps = decoded_route[4]

    assert len(steps) == 2
    assert steps[0][4] == 1_000_000
    assert steps[0][5] == 0
    assert steps[1][4] == 0
    assert steps[1][5] == 1_000_042


def test_validate_opportunity_uses_profit_and_repayment_not_fixed_slippage():
    engine = ExecutionEngine(_config())

    engine.validate_opportunity(
        {
            "loan_amount_usd": 100_000.0,
            "flashloan_fee_usd": 90.0,
            "gas_cost_usd": 0.50,
            "gross_profit_usd": 250.0,
            "final_output_usd": 100_250.0,
            "minimum_final_output": 100_092.0,
            "weakest_pool_tvl_usd": 1_000_000.0,
            "slippage_bps": engine.config.max_route_slippage_bps * 10.0,
        }
    )


def test_validate_opportunity_rejects_when_final_output_cannot_repay_floor():
    engine = ExecutionEngine(_config())

    with pytest.raises(ValueError, match="final output below repayment"):
        engine.validate_opportunity(
            {
                "loan_amount_usd": 100_000.0,
                "flashloan_fee_usd": 90.0,
                "gas_cost_usd": 0.50,
                "gross_profit_usd": 250.0,
                "final_output_usd": 100_050.0,
                "minimum_final_output": 100_092.0,
                "weakest_pool_tvl_usd": 1_000_000.0,
                "slippage_bps": 0.0,
            }
        )


def test_validate_opportunity_rejects_excessive_liquidity_impact():
    engine = ExecutionEngine(_config())

    with pytest.raises(ValueError, match="excessive liquidity impact"):
        engine.validate_opportunity(
            {
                "loan_amount_usd": 200_000.0,
                "flashloan_fee_usd": 180.0,
                "gas_cost_usd": 0.50,
                "gross_profit_usd": 500.0,
                "final_output_usd": 200_500.0,
                "minimum_final_output": 200_182.0,
                "weakest_pool_tvl_usd": 1_000_000.0,
            }
        )
