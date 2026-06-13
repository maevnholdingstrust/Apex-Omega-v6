from eth_abi import decode
from web3 import Web3

from apex_omega_core.core.execution_engine import ExecutionEngine
from apex_omega_core.core.expanded_strategy_steps import build_expanded_strategy_output_from_cycle
from apex_omega_core.core.route_graph import CycleRecord
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
        min_net_profit_usd=2.0,
        min_raw_spread_bps=1.0,
        max_route_slippage_bps=100.0,
        max_mempool_degradation_bps=200.0,
        min_pool_tvl_usd=10_000.0,
        max_trade_to_pool_ratio_bps=500.0,
        risk_buffer_usd=0.0,
        c1_gas_usd=0.38,
        c2_gas_usd=0.55,
        flash_loan_fee_bps=5.0,
        bundle_target_block_offset=1,
        bundle_max_block_window=5,
    )


def _cycle(*, dexes=None) -> CycleRecord:
    return CycleRecord(
        tokens=["UNI", "LINK", "UNI"],
        pools=["0xPool1", "0xPool2"],
        dexes=dexes or ["qsv2", "univ3_3000"],
        hop_count=2,
        amount_in=10.0,
        trade_size_usd=80.0,
        amount_out=10.5,
        gross_profit=0.5,
        gross_profit_usd=4.0,
        flash_fee_usd=0.04,
        gas_cost_usd=0.50,
        net_profit_usd=3.46,
        p_fill=0.99,
        e_profit=3.42,
        profitable=True,
        swap_0_to_1=[True, False],
        leg_amounts_in=[10.0, 20.0],
        leg_amounts_out=[20.0, 10.5],
    )


def test_expanded_strategy_builds_c1_payload_with_receiver_locked_to_c1():
    cfg = _config()
    build = build_expanded_strategy_output_from_cycle(
        _cycle(),
        {"UNI": 8.0},
        executor_address=cfg.c1_executor_address,
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is True
    assert build.strategy_output is not None
    assert build.strategy_output["flash_loan_receiver"] == Web3.to_checksum_address(cfg.c1_executor_address)

    plan = ExecutionEngine(cfg).build_c1_plan(build.strategy_output)
    decoded = decode(["address", "uint256", "uint256", "bytes"], plan.calldata[4:])
    assert Web3.to_checksum_address(decoded[0]) == Web3.to_checksum_address(build.strategy_output["asset"])
    assert decoded[1] == build.strategy_output["flash_loan_amount"]
    assert decoded[2] == build.strategy_output["min_profit"]
    assert len(decoded[3]) == build.compiled_payload_len


def test_expanded_strategy_fails_closed_for_unsupported_dex_label():
    build = build_expanded_strategy_output_from_cycle(
        _cycle(dexes=["curve_ss", "qsv2"]),
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is False
    assert build.reason == "expanded route leg is not payload-buildable"


def test_expanded_strategy_accepts_supported_v2_aliases_without_live_rpc():
    build = build_expanded_strategy_output_from_cycle(
        _cycle(dexes=["sushiswap_v2", "univ3_3000"]),
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is True
    assert build.strategy_output is not None
    assert build.strategy_output["steps"][0]["target"] == Web3.to_checksum_address(
        "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"
    )


def test_expanded_strategy_curve_requires_direction_metadata():
    cycle = _cycle(dexes=["curve_ss", "univ3_3000"])
    cycle.swap_0_to_1 = []
    build = build_expanded_strategy_output_from_cycle(
        cycle,
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is False
    assert build.reason == "Curve route missing coin direction metadata"
