from __future__ import annotations

import pytest

from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex


class _CaptureStore:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)
        return event


class _NoopTelegram:
    def send_event(self, event):
        return False


class _FakeW3:
    def is_connected(self):
        return False


def test_contract_invoker_records_dry_run_event(monkeypatch):
    invoker = ContractInvoker("0x1111111111111111111111111111111111111111")
    capture = _CaptureStore()
    invoker._state_store = capture
    invoker._telegram = _NoopTelegram()
    invoker.w3 = _FakeW3()
    monkeypatch.setattr(invoker, "_eth_call", lambda _calldata: {"ok": True, "output": "0x", "error": None})
    invoker.send_tx = False

    out = invoker.invoke(
        "0x1234",
        p_net_usd=12.5,
        execution_context={"chain_id": 137, "token_pair": "USDC/WPOL"},
    )

    assert out["success"] is True
    assert capture.events
    assert capture.events[-1]["status"] == "dry_run"
    assert capture.events[-1]["chain_id"] == 137
    assert capture.events[-1]["token_pair"] == "USDC/WPOL"


@pytest.mark.asyncio
async def test_c1_does_not_fabricate_tx_hash():
    strategy = C1AggressorApex()
    strategy.contract_invoker.build_c1_calldata = lambda _plan: "0x1234"
    strategy.contract_invoker.invoke = lambda _calldata: {"success": True, "tx_hash": None}
    strategy.sentinel.build_execution_slippage = lambda _sentinel_output: None

    result = await strategy.execute_contract_strike(
        {"action": "STRIKE", "sentinel_output": {"optimal_input": 1.0, "final_output": 1.0}}
    )
    assert result.success is True
    assert result.tx_hash is None


@pytest.mark.asyncio
async def test_c2_does_not_fabricate_tx_hash():
    strategy = C2SurgeonApex()
    strategy.contract_invoker.build_c2_calldata = lambda _plan: "0x1234"
    strategy.contract_invoker.invoke = lambda _calldata: {"success": True, "tx_hash": None}
    strategy.sentinel.build_execution_slippage = lambda _sentinel_output: None

    result = await strategy.execute_contract_decision(
        {"decision": "STRIKE", "sentinel_output": {"optimal_input": 1.0, "final_output": 1.0}}
    )
    assert result.success is True
    assert result.tx_hash is None
