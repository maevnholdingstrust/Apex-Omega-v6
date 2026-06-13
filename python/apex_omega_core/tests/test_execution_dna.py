from pathlib import Path

from apex_omega_core.core.execution_dna import build_execution_dna_cards, live_execution_blockers
from apex_omega_core.core.runtime_config import RuntimeConfig


def _config(**overrides):
    values = dict(
        chain_id=137,
        environment="test",
        live_trading_enabled=True,
        dry_run=False,
        polygon_rpc="https://polygon.invalid",
        polygon_wss="",
        polygon_private_mempool_rpc_url="https://private-submit.invalid",
        executor_private_key="0xabc",
        bundle_signer_private_key="",
        c1_executor_address="0x222F3B6b1ae90c279addA5b0eA0D8e87E49262Ac",
        c2_executor_address="0x8B04b0db6e803Bc29C3327885351D4297ABad9BE",
        liquidation_executor_address="0xF9a28f389Ad8c33F9da68c736BEAf1F2A3795a56",
        aave_v3_pool_address="0x1111111111111111111111111111111111111111",
        balancer_vault_address="",
        titan_mev_us_west="https://relay.invalid",
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
    values.update(overrides)
    return RuntimeConfig(**values)


def test_execution_dna_requires_artifact_backed_pool_state():
    cards = build_execution_dna_cards(
        limit=2,
        csv_path=Path("C:/tmp/apex_omega_missing_dry_run_results.csv"),
        config=_config(),
    )

    assert cards == []


def test_execution_dna_builds_no_broadcast_paired_payloads(tmp_path):
    csv_path = tmp_path / "dry_run_results.csv"
    csv_path.write_text(
        "\n".join(
            [
                "expected_net_edge,pair,buy_dex,sell_dex,buy_pool,sell_pool,fee1,r1_in,r1_out,fee2,r2_in,r2_out",
                "10.0,USDCe/WMATIC,quickswap_v2,uniswap_v3,0x1111111111111111111111111111111111111111,0x2222222222222222222222222222222222222222,0.003,1000000,2520000,0.003,2590000,1140000",
                "11.0,USDCe/WMATIC,quickswap_v2,uniswap_v3,0x3333333333333333333333333333333333333333,0x4444444444444444444444444444444444444444,0.003,1035000,2630000,0.003,2670000,1186000",
            ]
        ),
        encoding="utf-8",
    )
    cards = build_execution_dna_cards(
        limit=2,
        csv_path=csv_path,
        config=_config(),
    )

    assert len(cards) == 2
    assert cards[0]["broadcast"]["enabled"] is False
    assert cards[0]["cycle"]["c1"]["target"] == _config().c1_executor_address
    assert cards[0]["cycle"]["c2"]["target"] == _config().c2_executor_address
    assert cards[0]["payloads"]["c1"]["payload_bytes"] > 0
    assert cards[0]["payloads"]["c2"]["merkle_proof_required_for_live"] is True
    assert cards[0]["cycle"]["c2"]["decision"] == "POTENTIAL_STRIKE_AFTER_C1"


def test_live_execution_blockers_report_config_gates():
    blockers = live_execution_blockers(
        _config(
            live_trading_enabled=False,
            dry_run=True,
            polygon_rpc="",
            executor_private_key="",
            titan_mev_us_west="",
        )
    )

    assert "LIVE_TRADING_ENABLED is false" in blockers
    assert "DRY_RUN is true" in blockers
    assert "POLYGON_RPC" in blockers
    assert "EXECUTOR_PRIVATE_KEY" in blockers
