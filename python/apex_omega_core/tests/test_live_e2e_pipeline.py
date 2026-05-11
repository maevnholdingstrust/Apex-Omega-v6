from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from apex_omega_core.core.live_e2e_pipeline import run_live_e2e_cycle
from apex_omega_core.core.runtime_config import RuntimeConfig


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        chain_id=137,
        environment="test",
        live_trading_enabled=False,
        dry_run=True,
        polygon_rpc="https://polygon-rpc.com/",
        polygon_wss="wss://polygon-ws.invalid",
        executor_private_key="",
        bundle_signer_private_key="",
        c1_executor_address="0x1111111111111111111111111111111111111111",
        c2_executor_address="0x2222222222222222222222222222222222222222",
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


@dataclass
class _FakeSubmission:
    relay: str
    status: str


class _FakeWatcher:
    async def capture_snapshot(self, duration_s: float = 1.5):  # noqa: ARG002
        return SimpleNamespace(
            pending_swap_count=2,
            competing_bot_density=0.5,
            reserve_delta_forecast={"0xrouter": 1.0},
            snapshot_timestamp_ms=1234,
        )


def test_live_e2e_returns_no_candidate(monkeypatch):
    import apex_omega_core.core.live_e2e_pipeline as mod

    monkeypatch.setattr(
        mod,
        "run_scanner_strategy_pipeline",
        lambda **_kwargs: SimpleNamespace(scanned=0, candidates=[]),
    )

    result = asyncio.run(
        run_live_e2e_cycle(
            config=_config(),
            watcher=_FakeWatcher(),
            submit_live=False,
        )
    )

    assert result["mode"] == "no_candidate"
    assert result["mempool"]["pending_swap_count"] == 2


def test_live_e2e_simulate_only(monkeypatch):
    import apex_omega_core.core.live_e2e_pipeline as mod

    strikeable = SimpleNamespace(
        reason="ok",
        opportunity=SimpleNamespace(
            buy_venue="quickswap_v2",
            sell_venue="uniswap_v3",
            raw_spread_bps=120.0,
        ),
        build=SimpleNamespace(
            strikeable=True,
            strategy_output={
                "asset": "0x7777777777777777777777777777777777777777",
                "min_profit": 10,
                "flash_loan_amount": 1_000_000,
                "steps": [
                    {
                        "protocol": 1,
                        "target": "0x5555555555555555555555555555555555555555",
                        "approveToken": "0x7777777777777777777777777777777777777777",
                        "outputToken": "0x8888888888888888888888888888888888888888",
                        "callValue": 0,
                        "minAmountIn": 1_000_000,
                        "minAmountOut": 990_000,
                        "feeBps": 30,
                        "data": b"\x12\x34\x56\x78" + (1_000_000).to_bytes(32, "big"),
                    }
                ],
                "opportunity": {"net_profit_usd": 5.0},
            },
        ),
    )
    monkeypatch.setattr(
        mod,
        "run_scanner_strategy_pipeline",
        lambda **_kwargs: SimpleNamespace(scanned=1, candidates=[strikeable]),
    )

    result = asyncio.run(
        run_live_e2e_cycle(
            config=_config(),
            watcher=_FakeWatcher(),
            submit_live=False,
        )
    )

    assert result["mode"] == "simulate_only"
    assert result["submission"]["attempted"] is False
    assert result["payload"]["compiled_payload_bytes"] > 0
    assert result["simulation"]["target"] == "institutional"


def test_live_e2e_submit_blocked(monkeypatch):
    import apex_omega_core.core.live_e2e_pipeline as mod

    strikeable = SimpleNamespace(
        reason="ok",
        opportunity=SimpleNamespace(
            buy_venue="quickswap_v2",
            sell_venue="uniswap_v3",
            raw_spread_bps=120.0,
        ),
        build=SimpleNamespace(
            strikeable=True,
            strategy_output={
                "asset": "0x7777777777777777777777777777777777777777",
                "min_profit": 10,
                "flash_loan_amount": 1_000_000,
                "steps": [
                    {
                        "protocol": 1,
                        "target": "0x5555555555555555555555555555555555555555",
                        "approveToken": "0x7777777777777777777777777777777777777777",
                        "outputToken": "0x8888888888888888888888888888888888888888",
                        "callValue": 0,
                        "minAmountIn": 1_000_000,
                        "minAmountOut": 990_000,
                        "feeBps": 30,
                        "data": b"\x12\x34\x56\x78" + (1_000_000).to_bytes(32, "big"),
                    }
                ],
                "opportunity": {"net_profit_usd": 5.0},
            },
        ),
    )
    monkeypatch.setattr(
        mod,
        "run_scanner_strategy_pipeline",
        lambda **_kwargs: SimpleNamespace(scanned=1, candidates=[strikeable]),
    )

    result = asyncio.run(
        run_live_e2e_cycle(
            config=_config(),
            watcher=_FakeWatcher(),
            submit_live=True,
        )
    )

    assert result["mode"] == "blocked"
    assert result["submission"]["attempted"] is False


def test_live_e2e_submit_unblocked(monkeypatch):
    import apex_omega_core.core.live_e2e_pipeline as mod

    strikeable = SimpleNamespace(
        reason="ok",
        opportunity=SimpleNamespace(
            buy_venue="quickswap_v2",
            sell_venue="uniswap_v3",
            raw_spread_bps=120.0,
        ),
        build=SimpleNamespace(
            strikeable=True,
            strategy_output={"opportunity": {"net_profit_usd": 100.0}},
        ),
    )

    class _FakeEngine:
        def __init__(self, cfg):  # noqa: ARG002
            pass

        def validate_opportunity(self, _opp):
            return None

        def build_c1_plan(self, _out):
            return SimpleNamespace(
                compiled=SimpleNamespace(encoded_payload=b"\x01\x02", min_profit=10, asset="0xasset"),
                calldata=b"\x12\x34\x56\x78",
                flash_loan_amount=1_000_000,
                target="institutional",
            )

        def simulate_only(self, _plan):
            return {"target": "institutional"}

        def sign_transaction(self, _plan):
            return "0xdeadbeef"

        def execute_bundle(self, _raw_tx):
            return [_FakeSubmission(relay="fastlane", status="submitted")]

    cfg = _config()
    cfg = RuntimeConfig(
        **{
            **cfg.__dict__,
            "live_trading_enabled": True,
            "dry_run": False,
            "executor_private_key": "0xabc",
            "titan_mev_us_west": "https://polygon-rpc.com/",
        }
    )
    monkeypatch.setattr(mod, "ExecutionEngine", _FakeEngine)
    monkeypatch.setattr(
        mod,
        "run_scanner_strategy_pipeline",
        lambda **_kwargs: SimpleNamespace(scanned=1, candidates=[strikeable]),
    )
    monkeypatch.setattr(mod, "live_execution_blockers", lambda _cfg: [])

    result = asyncio.run(
        run_live_e2e_cycle(
            config=cfg,
            watcher=_FakeWatcher(),
            submit_live=True,
        )
    )

    assert result["mode"] == "submitted"
    assert result["submission"]["attempted"] is True
    assert result["submission"]["results"][0]["relay"] == "fastlane"
