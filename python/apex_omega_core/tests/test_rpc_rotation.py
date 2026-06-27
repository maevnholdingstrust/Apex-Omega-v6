from __future__ import annotations

import json
import time

import pytest

from apex_omega_core.core import rpc_rotation
from apex_omega_core.core.rpc_rotation import RpcRotationError, RpcRotationManager, collect_rpc_urls


class _FakeEth:
    def __init__(self, chain_id: int, block_number: int) -> None:
        self.chain_id = chain_id
        self.block_number = block_number


class _FakeWeb3:
    def __init__(self, *, connected: bool, chain_id: int, block_number: int) -> None:
        self._connected = connected
        self.eth = _FakeEth(chain_id, block_number)

    def is_connected(self) -> bool:
        return self._connected


def test_collect_rpc_urls_dedupes_env_discovered_and_defaults(monkeypatch):
    for key in rpc_rotation.RPC_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYGON_RPC", "https://primary.example")
    monkeypatch.setenv("POLYGON_RPC_URL", "https://primary.example")
    monkeypatch.setenv("PRIVATE_RPC_URL", "https://private.example")
    monkeypatch.setattr(
        rpc_rotation,
        "discover_public_rpc_urls",
        lambda chain_id: ["https://private.example", "https://discovered.example"],
    )

    urls = collect_rpc_urls()

    assert urls[:3] == [
        "https://primary.example",
        "https://private.example",
        "https://discovered.example",
    ]
    assert urls.count("https://primary.example") == 1


def test_rotation_rejects_wrong_chain_and_selects_fresh_polygon_endpoint():
    fixtures = {
        "https://mainnet.example": _FakeWeb3(connected=True, chain_id=1, block_number=99),
        "https://slow-polygon.example": _FakeWeb3(connected=True, chain_id=137, block_number=100),
        "https://fresh-polygon.example": _FakeWeb3(connected=True, chain_id=137, block_number=105),
    }

    def factory(url: str, timeout_s: float):  # noqa: ARG001
        return fixtures[url]

    manager = RpcRotationManager(fixtures.keys(), web3_factory=factory, cooldown_s=0.01, persist_state=False)

    w3, url = manager.get_web3()

    assert url == "https://fresh-polygon.example"
    assert w3.eth.chain_id == 137
    assert manager.summary()["healthy_count"] == 2
    wrong = next(state for state in manager.report() if state.url == "https://mainnet.example")
    assert wrong.status == "wrong_chain"


def test_rotation_errors_when_all_endpoints_fail():
    fixtures = {
        "https://dead.example": _FakeWeb3(connected=False, chain_id=0, block_number=0),
        "https://wrong.example": _FakeWeb3(connected=True, chain_id=1, block_number=100),
    }

    def factory(url: str, timeout_s: float):  # noqa: ARG001
        return fixtures[url]

    manager = RpcRotationManager(fixtures.keys(), web3_factory=factory, cooldown_s=0.01, persist_state=False)

    with pytest.raises(RpcRotationError, match="No healthy Polygon RPC endpoint"):
        manager.get_web3()


def test_rate_limit_failure_learns_cooldown_and_persists(tmp_path):
    fixtures = {
        "https://limited.example": _FakeWeb3(connected=True, chain_id=137, block_number=105),
    }

    def factory(url: str, timeout_s: float):  # noqa: ARG001
        return fixtures[url]

    state_path = tmp_path / "rpc_rotation_state.json"
    manager = RpcRotationManager(
        fixtures.keys(),
        web3_factory=factory,
        cooldown_s=1.0,
        preemptive_request_limit=20,
        state_path=state_path,
        persist_state=True,
    )
    manager.get_web3()
    manager.mark_failure("https://limited.example", "HTTP Error 429: too many requests")

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    endpoint = saved["endpoints"]["https://limited.example"]
    assert endpoint["status"] == "rate_limited"
    assert endpoint["adaptive_cooldown_s"] >= 2.0
    assert endpoint["preemptive_request_limit"] < 20

    reloaded = RpcRotationManager(
        fixtures.keys(),
        web3_factory=factory,
        cooldown_s=1.0,
        preemptive_request_limit=20,
        state_path=state_path,
        persist_state=True,
    )
    state = next(item for item in reloaded.report() if item.url == "https://limited.example")
    assert state.status == "rate_limited"
    assert state.rate_limit_failures == 1
    assert state.cooldown_until > 0.0


def test_preemptive_dispatch_limit_rotates_before_learned_limit():
    fixtures = {
        "https://near-limit.example": _FakeWeb3(connected=True, chain_id=137, block_number=106),
        "https://reserve.example": _FakeWeb3(connected=True, chain_id=137, block_number=105),
    }

    def factory(url: str, timeout_s: float):  # noqa: ARG001
        return fixtures[url]

    manager = RpcRotationManager(
        fixtures.keys(),
        web3_factory=factory,
        cooldown_s=1.0,
        preemptive_request_limit=100,
        persist_state=False,
    )
    manager.refresh(force=True)
    near_limit = manager._states["https://near-limit.example"]  # noqa: SLF001
    near_limit.preemptive_request_limit = 2
    near_limit.dispatch_window_started_at = time.monotonic()
    near_limit.dispatches_in_window = 1
    manager._cursor = 0  # noqa: SLF001

    _, url = manager.get_web3()

    assert url == "https://reserve.example"
    assert near_limit.cooldown_until > 0.0
    assert "preemptive rotation" in near_limit.last_error
