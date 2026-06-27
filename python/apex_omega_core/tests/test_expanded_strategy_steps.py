from eth_abi import decode
from web3 import Web3

from apex_omega_core.core.execution_engine import ExecutionEngine
import apex_omega_core.core.expanded_strategy_steps as expanded_strategy_steps
from apex_omega_core.core.expanded_strategy_steps import build_expanded_strategy_output_from_cycle
from apex_omega_core.core.route_graph import CycleRecord
from apex_omega_core.core.runtime_config import RuntimeConfig
from apex_omega_core.core.swap_adapters import SwapRequest, UniversalSwapAdapter


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
        min_flash_loan_usd=1_000.0,
        max_flash_loan_usd=100_000.0,
        autonomous_max_flashloan_cap_usd=100_000.0,
        risk_buffer_usd=0.0,
        c1_gas_usd=0.38,
        c2_gas_usd=0.55,
        flash_loan_fee_bps=5.0,
        bundle_target_block_offset=1,
        bundle_max_block_window=5,
    )


def _cycle(*, dexes=None, pools=None) -> CycleRecord:
    return CycleRecord(
        tokens=["UNI", "LINK", "UNI"],
        pools=pools or ["0xPool1", "0xPool2"],
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


def test_expanded_strategy_chains_guaranteed_leg_output_into_next_leg(monkeypatch):
    cfg = _config()
    expected_second_input = int(20 * 10**18 * 0.995)

    def fake_v2_quote(_w3, _router, amount_in, _token_in, _token_out):
        if amount_in == int(10 * 10**18):
            return int(20 * 10**18)
        assert amount_in == expected_second_input
        return int(11 * 10**18)

    monkeypatch.setattr(expanded_strategy_steps, "_v2_live_amount_out", fake_v2_quote)
    build = build_expanded_strategy_output_from_cycle(
        _cycle(dexes=["qsv2", "sushiswap_v2"]),
        {"UNI": 8.0},
        executor_address=cfg.c1_executor_address,
        min_net_profit_usd=2.0,
        rpc_url="http://127.0.0.1:8545",
    )

    assert build.strikeable is True
    assert build.strategy_output is not None
    assert build.strategy_output["steps"][1]["minAmountIn"] == expected_second_input
    assert build.diagnostics["live_quotes"][1]["amount_in"] == expected_second_input


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


def test_curve_adapter_uses_underlying_exchange_when_requested():
    step = UniversalSwapAdapter().build_step(
        SwapRequest(
            "curve",
            "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            1_000_000,
            990_000,
            "0x1111111111111111111111111111111111111111",
            1_900_000_000,
            pool="0x445FE580eF8d70FF569aB36e80c647af338db351",
            extra={"i": 2, "j": 1, "exchange_fn": "exchange_underlying"},
        )
    )

    assert bytes(step["data"])[:4] == Web3.keccak(text="exchange_underlying(int128,int128,uint256,uint256)")[:4]


def test_expanded_strategy_balancer_requires_pool_id_metadata():
    build = build_expanded_strategy_output_from_cycle(
        _cycle(dexes=["balancer_v2", "univ3_3000"]),
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is False
    assert build.reason == "Balancer route missing pool_id metadata"


def test_expanded_strategy_balancer_builds_with_pool_id_metadata():
    cycle = _cycle(
        dexes=["balancer_v2", "univ3_3000"],
        pools=["0x5555555555555555555555555555555555555555", "0x6666666666666666666666666666666666666666"],
    )
    cycle.pool_ids = ["0x" + "11" * 32, None]
    build = build_expanded_strategy_output_from_cycle(
        cycle,
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is True
    assert build.strategy_output is not None
    assert build.strategy_output["steps"][0]["target"] == Web3.to_checksum_address(
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
    )


def test_expanded_strategy_algebra_builds_with_router_metadata():
    build = build_expanded_strategy_output_from_cycle(
        _cycle(dexes=["quickswap_v3_algebra", "univ3_3000"]),
        {"UNI": 8.0},
        executor_address="0x1111111111111111111111111111111111111111",
        min_net_profit_usd=2.0,
    )

    assert build.strikeable is True
    assert build.strategy_output is not None
    assert build.strategy_output["steps"][0]["target"] == Web3.to_checksum_address(
        "0xf5b509bB0909a69B1c207E495f687a596C168E12"
    )
