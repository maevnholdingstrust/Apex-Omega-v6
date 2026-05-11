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
        executor_private_key="0xabc",
        bundle_signer_private_key="",
        c1_executor_address="0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD",
        c2_executor_address="0x0466759822ABAA7E416276E1cf2b538d7FC540BD",
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


def test_execution_dna_builds_no_broadcast_paired_payloads():
    cards = build_execution_dna_cards(
        limit=2,
        csv_path=Path("C:/tmp/apex_omega_missing_dry_run_results.csv"),
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
