from apex_omega_core.core.readiness_report import ComponentStatus, build_readiness_report
from apex_omega_core.core.runtime_config import RuntimeConfig


def _config(**overrides):
    values = dict(
        chain_id=137,
        environment="test",
        live_trading_enabled=False,
        dry_run=True,
        polygon_rpc="",
        polygon_wss="",
        executor_private_key="",
        bundle_signer_private_key="",
        c1_executor_address="",
        c2_executor_address="",
        aave_v3_pool_address="",
        balancer_vault_address="",
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
    values.update(overrides)
    return RuntimeConfig(**values)


def test_readiness_report_uses_component_and_live_env_gates(monkeypatch):
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._rust_status",
        lambda: ComponentStatus("native_math", True, "ok"),
    )
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._redis_status",
        lambda _config: ComponentStatus("redis_cache", True, "ok"),
    )
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._module_status",
        lambda _module, name, detail: ComponentStatus(name, True, detail),
    )

    report = build_readiness_report(_config(live_trading_enabled=True, dry_run=False))

    assert report.production_ready is False
    assert set(report.missing_live_env) >= {
        "POLYGON_RPC",
        "EXECUTOR_PRIVATE_KEY",
        "C1_INSTITUTIONAL_EXECUTOR_ADDRESS",
        "C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS",
        "AAVE_V3_POOL_ADDRESS",
    }


def test_readiness_report_serializes_component_status(monkeypatch):
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._rust_status",
        lambda: ComponentStatus("native_math", True, "ok"),
    )
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._redis_status",
        lambda _config: ComponentStatus("redis_cache", True, "ok"),
    )
    monkeypatch.setattr(
        "apex_omega_core.core.readiness_report._module_status",
        lambda _module, name, detail: ComponentStatus(name, True, detail),
    )

    data = build_readiness_report(_config()).as_dict()

    assert data["production_ready"] is True
    assert data["chain_id"] == 137
    assert any(component["name"] == "payload_compiler" for component in data["components"])
