import asyncio

import pytest

from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex


class _FakeInvoker:
    def __init__(self, *, send_tx: bool, private_key: str, rpc_url: str, invocation: dict):
        self.send_tx = send_tx
        self.private_key = private_key
        self.rpc_url = rpc_url
        self._invocation = invocation

    def build_c1_calldata(self, _plan: dict) -> str:
        return "0x1234"

    def build_c2_calldata(self, _plan: dict) -> str:
        return "0x5678"

    def invoke(self, _calldata: str) -> dict:
        return dict(self._invocation)


def _enable_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")


def test_c1_execute_contract_strike_fails_closed_without_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live_env(monkeypatch)
    c1 = C1AggressorApex()
    c1.contract_invoker = _FakeInvoker(
        send_tx=True,
        private_key="",
        rpc_url="https://polygon-rpc.com/",
        invocation={},
    )
    plan = {
        "action": "STRIKE",
        "sentinel_output": {"profit": 1.0, "optimal_input": 100.0, "final_output": 101.0},
    }
    result = asyncio.run(c1.execute_contract_strike(plan))
    assert result.success is False
    assert result.revert_reason is not None
    assert "PRIVATE_KEY" in result.revert_reason


def test_c1_execute_contract_strike_returns_receipt_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live_env(monkeypatch)
    c1 = C1AggressorApex()
    c1.contract_invoker = _FakeInvoker(
        send_tx=True,
        private_key="0x" + "11" * 32,
        rpc_url="https://polygon-rpc.com/",
        invocation={
            "success": True,
            "simulation_only": False,
            "tx_hash": "0xabc",
            "executed_onchain": True,
            "broadcast": {"status": 1, "gasUsed": 123456},
            "simulation": {"error": None},
        },
    )
    plan = {
        "action": "STRIKE",
        "sentinel_output": {"profit": 1.0, "optimal_input": 100.0, "final_output": 101.0},
    }
    result = asyncio.run(c1.execute_contract_strike(plan))
    assert result.success is True
    assert result.tx_hash == "0xabc"
    assert result.receipt_status == 1
    assert result.gas_used == 123456
    assert result.accepted is True
    assert result.executed_onchain is True


def test_c2_execute_contract_decision_rejects_simulation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live_env(monkeypatch)
    c2 = C2SurgeonApex()
    c2.contract_invoker = _FakeInvoker(
        send_tx=True,
        private_key="0x" + "22" * 32,
        rpc_url="https://polygon-rpc.com/",
        invocation={
            "success": True,
            "simulation_only": True,
            "tx_hash": None,
            "broadcast": {"status": "not_sent", "reason": "APEX_SEND_TX != 1"},
            "simulation": {"error": None},
        },
    )
    plan = {"decision": "STRIKE", "sentinel_output": {"profit": 1.0}}
    result = asyncio.run(c2.execute_contract_decision(plan))
    assert result.success is False
    assert result.revert_reason == "transaction not accepted: simulation_only result"
