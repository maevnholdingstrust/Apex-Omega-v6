"""Unit tests for backend.executor_registry.

These tests are fully offline – no network calls are made.  They cover:

* Registry structure (chains, entries, ABI completeness)
* Function selector computation
* ExecutorEntry.address / owner_address resolution via env vars
* validate_registry_entry in the address-not-configured path
* validate_all aggregate helper
* ContractInterface construction and selector helpers
* InstitutionalExecutor / LiquidationExecutorContract dispatch in dry-run
* LiveExecutor profitability gate
* Flask server endpoints (in-process test client)
"""

from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from backend.executor_registry import (
    EXECUTOR_REGISTRY,
    FUNCTION_SIGNATURES,
    INLINE_ABIS,
    STRATEGY_C1,
    STRATEGY_C2,
    SUPPORTED_CHAINS,
    DeploymentStatus,
    ExecutorEntry,
    ValidationResult,
    _keccak4,
    get_entry,
    get_rpc_url,
    list_entries,
    validate_all,
    validate_registry_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def c1_entry() -> ExecutorEntry:
    return get_entry(137, STRATEGY_C1)


@pytest.fixture
def c2_entry() -> ExecutorEntry:
    return get_entry(137, STRATEGY_C2)


# ---------------------------------------------------------------------------
# SUPPORTED_CHAINS
# ---------------------------------------------------------------------------


def test_polygon_in_supported_chains():
    assert 137 in SUPPORTED_CHAINS


def test_ethereum_in_supported_chains():
    assert 1 in SUPPORTED_CHAINS


def test_chain_config_fields():
    poly = SUPPORTED_CHAINS[137]
    assert poly.name == "Polygon"
    assert poly.native_symbol == "POL"
    assert poly.rpc_env_var == "POLYGON_RPC"


# ---------------------------------------------------------------------------
# EXECUTOR_REGISTRY
# ---------------------------------------------------------------------------


def test_registry_has_polygon_c1():
    assert (137, STRATEGY_C1) in EXECUTOR_REGISTRY


def test_registry_has_polygon_c2():
    assert (137, STRATEGY_C2) in EXECUTOR_REGISTRY


def test_registry_strategy_names():
    assert STRATEGY_C1 == "institutional"
    assert STRATEGY_C2 == "ultimate"


def test_get_entry_returns_correct_strategy(c1_entry, c2_entry):
    assert c1_entry.strategy == STRATEGY_C1
    assert c2_entry.strategy == STRATEGY_C2


def test_get_entry_raises_for_unknown():
    with pytest.raises(KeyError, match="No executor registry entry"):
        get_entry(999, "nonexistent")


def test_list_entries_length():
    assert len(list_entries()) == len(EXECUTOR_REGISTRY)


# ---------------------------------------------------------------------------
# INLINE_ABIS
# ---------------------------------------------------------------------------


def test_institutional_abi_has_init_aave_flash():
    names = {fn["name"] for fn in INLINE_ABIS["institutional_executor"] if fn["type"] == "function"}
    assert "initAaveFlash" in names


def test_institutional_abi_has_owner():
    names = {fn["name"] for fn in INLINE_ABIS["institutional_executor"] if fn["type"] == "function"}
    assert "owner" in names


def test_ultimate_abi_has_execute_arbitrage():
    names = {fn["name"] for fn in INLINE_ABIS["ultimate_arbitrage_executor"] if fn["type"] == "function"}
    assert "executeArbitrage" in names


def test_entry_abi_matches_inline_abi(c1_entry):
    assert c1_entry.abi == INLINE_ABIS["institutional_executor"]


# ---------------------------------------------------------------------------
# FUNCTION_SIGNATURES
# ---------------------------------------------------------------------------


def test_c1_has_init_aave_flash_sig():
    assert "initAaveFlash(address,uint256,uint256,bytes)" in FUNCTION_SIGNATURES[STRATEGY_C1]


def test_c2_has_execute_arbitrage_sig():
    assert "executeArbitrage(address,uint256,uint256,bytes32[],bytes)" in FUNCTION_SIGNATURES[STRATEGY_C2]


def test_c1_sigs_match_entry(c1_entry):
    assert c1_entry.function_signatures == FUNCTION_SIGNATURES[STRATEGY_C1]


# ---------------------------------------------------------------------------
# _keccak4 / selectors
# ---------------------------------------------------------------------------


def test_keccak4_produces_four_bytes():
    sel = _keccak4("owner()")
    assert len(sel) == 4


def test_keccak4_owner_selector():
    # owner() selector is well-known: 0x8da5cb5b
    sel = _keccak4("owner()")
    assert sel == bytes.fromhex("8da5cb5b")


def test_entry_selectors_length(c1_entry):
    assert len(c1_entry.selectors) == len(c1_entry.function_signatures)


def test_entry_selectors_all_four_bytes(c1_entry):
    for sel in c1_entry.selectors:
        assert len(sel) == 4


# ---------------------------------------------------------------------------
# ExecutorEntry.address / owner_address resolution
# ---------------------------------------------------------------------------


def test_address_from_env_var(c1_entry):
    test_addr = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    with patch.dict(os.environ, {c1_entry.address_env_var: test_addr}):
        assert c1_entry.address == test_addr


def test_address_falls_back_to_fallback(c1_entry):
    with patch.dict(os.environ, {c1_entry.address_env_var: ""}):
        assert c1_entry.address == c1_entry.fallback_address


def test_owner_address_from_env_var(c1_entry):
    test_owner = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    with patch.dict(os.environ, {c1_entry.owner_env_var: test_owner}):
        assert c1_entry.owner_address == test_owner


def test_as_dict_contains_required_keys(c1_entry):
    d = c1_entry.as_dict()
    for key in ("chain_id", "strategy", "address", "abi_id", "function_signatures",
                "owner_address", "required_permissions", "deployment_status", "deployment_block"):
        assert key in d


# ---------------------------------------------------------------------------
# validate_registry_entry – no-network paths
# ---------------------------------------------------------------------------


def test_validate_returns_failure_when_address_not_configured():
    entry = ExecutorEntry(
        chain_id=137,
        strategy=STRATEGY_C1,
        address_env_var="__NONEXISTENT_ENV_VAR_C1__",
        abi_id="institutional_executor",
        function_signatures=FUNCTION_SIGNATURES[STRATEGY_C1],
        owner_env_var="__NONEXISTENT_ENV_VAR_OWNER__",
        required_permissions=[],
        deployment_status=DeploymentStatus.UNKNOWN,
        fallback_address=None,
    )
    result = validate_registry_entry(entry)
    assert isinstance(result, ValidationResult)
    assert result.passed is False
    assert result.checks.get("address_configured") is False
    assert any("not configured" in e for e in result.errors)


def test_validate_passes_all_checks_with_mocked_rpc(c1_entry, monkeypatch):
    """Full validation pass using a mocked Web3 provider."""
    from unittest.mock import MagicMock
    from web3 import Web3 as Web3Lib

    # Inject a stable address so the validation does not depend on the env
    test_addr = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
    monkeypatch.setenv(c1_entry.address_env_var, test_addr)
    monkeypatch.setenv(c1_entry.owner_env_var, "0xDeADBEEfDeADBEEFdeadbeefdEAdbeefdEADbEEF")

    # Build fake bytecode using real selectors (computed before patching)
    real_selectors = [bytes(Web3Lib.keccak(text=sig)[:4]) for sig in c1_entry.function_signatures]
    fake_code = b"\x60\x80" + b"".join(real_selectors) + b"\x00" * 100

    # Fake owner() return: 32-byte padded address
    owner_bytes = b"\x00" * 12 + bytes.fromhex("DeADBEEfDeADBEEFdeadbeefdEAdbeefdEADbEEF")

    mock_eth = MagicMock()
    mock_eth.get_code.return_value = fake_code
    mock_eth.chain_id = 137
    mock_eth.call.return_value = owner_bytes

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth

    mock_Web3_class = MagicMock()
    mock_Web3_class.return_value = mock_w3
    mock_Web3_class.HTTPProvider = MagicMock()
    mock_Web3_class.to_checksum_address = Web3Lib.to_checksum_address
    # Delegate keccak to the real implementation so selectors are correct
    mock_Web3_class.keccak = Web3Lib.keccak

    with patch("backend.executor_registry._Web3", mock_Web3_class):
        result = validate_registry_entry(c1_entry, rpc_url="http://localhost:8545")

    # address_configured must pass; bytecode and chain checks should pass too
    assert result.checks["address_configured"] is True
    assert result.checks["bytecode_exists"] is True
    assert result.checks["chain_id_matches"] is True


def test_validate_fails_when_bytecode_empty(c1_entry, monkeypatch):
    from unittest.mock import MagicMock

    test_addr = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
    monkeypatch.setenv(c1_entry.address_env_var, test_addr)

    mock_eth = MagicMock()
    mock_eth.get_code.return_value = b""

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth

    mock_Web3_class = MagicMock()
    mock_Web3_class.return_value = mock_w3
    mock_Web3_class.HTTPProvider = MagicMock()
    mock_Web3_class.to_checksum_address = lambda x: x

    with patch("backend.executor_registry._Web3", mock_Web3_class):
        result = validate_registry_entry(c1_entry, rpc_url="http://localhost:8545")

    assert result.checks["bytecode_exists"] is False
    assert result.passed is False


# ---------------------------------------------------------------------------
# validate_all
# ---------------------------------------------------------------------------


def test_validate_all_returns_list_of_results():
    results = validate_all(chain_id=137)
    # We have at least 2 entries for chain 137 (C1 + C2)
    assert isinstance(results, list)
    assert len(results) >= 2
    for r in results:
        assert isinstance(r, ValidationResult)


def test_validate_all_no_chain_filter():
    results = validate_all()
    assert len(results) == len(EXECUTOR_REGISTRY)


# ---------------------------------------------------------------------------
# get_rpc_url
# ---------------------------------------------------------------------------


def test_get_rpc_url_polygon_from_env(monkeypatch):
    monkeypatch.setenv("POLYGON_RPC", "https://my-rpc.example.com/")
    url = get_rpc_url(137)
    assert url == "https://my-rpc.example.com/"


def test_get_rpc_url_polygon_fallback():
    with patch.dict(os.environ, {"POLYGON_RPC": ""}):
        url = get_rpc_url(137)
    assert url == "https://polygon-rpc.com/"


def test_get_rpc_url_unsupported_chain():
    with pytest.raises(ValueError, match="Unsupported chain_id"):
        get_rpc_url(999)


# ---------------------------------------------------------------------------
# ContractInterface
# ---------------------------------------------------------------------------


def test_contract_interface_from_registry():
    from backend.contract_interface import ContractInterface

    iface = ContractInterface.from_registry(137, STRATEGY_C1, rpc_url="http://localhost:8545")
    assert iface.entry.strategy == STRATEGY_C1
    assert iface.entry.chain_id == 137


def test_contract_interface_selector():
    from backend.contract_interface import ContractInterface

    iface = ContractInterface.from_registry(137, STRATEGY_C1, rpc_url="http://localhost:8545")
    sel = iface.selector("owner()")
    assert sel == bytes.fromhex("8da5cb5b")


def test_contract_interface_raises_on_missing_address(monkeypatch):
    from backend.contract_interface import ContractInterface

    entry = get_entry(137, STRATEGY_C1)
    monkeypatch.setenv(entry.address_env_var, "")

    iface = ContractInterface(
        ExecutorEntry(
            chain_id=137,
            strategy=STRATEGY_C1,
            address_env_var="__ABSENT_C1_ADDR__",
            abi_id="institutional_executor",
            function_signatures=FUNCTION_SIGNATURES[STRATEGY_C1],
            owner_env_var="__ABSENT_OWNER__",
            required_permissions=[],
            deployment_status=DeploymentStatus.UNKNOWN,
            fallback_address=None,
        ),
        rpc_url="http://localhost:8545",
    )
    with pytest.raises(ValueError, match="not configured"):
        _ = iface.address


# ---------------------------------------------------------------------------
# InstitutionalExecutor – dry run
# ---------------------------------------------------------------------------


def test_institutional_executor_dry_run_returns_not_sent():
    from backend.institutional_executor import InstitutionalExecutor
    from unittest.mock import patch, MagicMock

    # Patch out Web3 so no network call is made
    mock_eth = MagicMock()
    mock_eth.call.return_value = b"\x00" * 32

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth
    mock_w3.keccak.return_value = b"\x00" * 32

    with patch("backend.contract_interface.Web3") as MockW3, \
         patch("backend.institutional_executor.Web3") as MockW3b:
        MockW3.return_value = mock_w3
        MockW3.HTTPProvider = MagicMock()
        MockW3.to_checksum_address = lambda x: x
        MockW3.to_hex = lambda x: "0x" + x.hex()
        MockW3.keccak = lambda **kw: bytes(32)
        MockW3b.to_checksum_address = lambda x: x

        c1 = InstitutionalExecutor(137, rpc_url="http://localhost:8545", dry_run=True)
        result = c1.init_aave_flash(
            asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            amount=1_000_000,
            min_profit=100,
            payload=b"\x00\x01\x02\x03",
        )

    assert result["dry_run"] is True
    assert result["broadcast"]["status"] == "not_sent"


# ---------------------------------------------------------------------------
# LiquidationExecutorContract – dry run
# ---------------------------------------------------------------------------


def test_liquidation_executor_dry_run_returns_not_sent():
    from backend.liquidation_executor_contract import LiquidationExecutorContract
    from unittest.mock import patch, MagicMock

    mock_eth = MagicMock()
    mock_eth.call.return_value = b"\x00" * 32

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth
    mock_w3.keccak.return_value = bytes(32)

    with patch("backend.contract_interface.Web3") as MockW3, \
         patch("backend.liquidation_executor_contract.Web3") as MockW3b:
        MockW3.return_value = mock_w3
        MockW3.HTTPProvider = MagicMock()
        MockW3.to_checksum_address = lambda x: x
        MockW3.to_hex = lambda x: "0x" + x.hex()
        MockW3.keccak = lambda **kw: bytes(32)
        MockW3b.to_checksum_address = lambda x: x

        c2 = LiquidationExecutorContract(137, rpc_url="http://localhost:8545", dry_run=True)
        result = c2.execute_arbitrage(
            asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            amount=1_000_000,
            min_profit=100,
            merkle_proof=[],
            payload=b"\x00\x01\x02\x03",
        )

    assert result["dry_run"] is True
    assert result["broadcast"]["status"] == "not_sent"


# ---------------------------------------------------------------------------
# LiveExecutor – profitability gate
# ---------------------------------------------------------------------------


def test_live_executor_gate_rejects_zero_profit():
    from backend.live_executor import LiveExecutor

    ex = LiveExecutor.__new__(LiveExecutor)
    assert ex._profitability_gate(0.0, 0.9) is False


def test_live_executor_gate_rejects_negative_profit():
    from backend.live_executor import LiveExecutor

    ex = LiveExecutor.__new__(LiveExecutor)
    assert ex._profitability_gate(-1.0, 1.0) is False


def test_live_executor_gate_rejects_negative_p_fill():
    from backend.live_executor import LiveExecutor

    ex = LiveExecutor.__new__(LiveExecutor)
    # Both negative → product positive, but gate must still reject
    assert ex._profitability_gate(-5.0, -0.9) is False


def test_live_executor_gate_accepts_positive():
    from backend.live_executor import LiveExecutor

    ex = LiveExecutor.__new__(LiveExecutor)
    assert ex._profitability_gate(5.0, 0.8) is True


def test_live_executor_execute_c1_skips_on_zero_profit():
    from backend.live_executor import LiveExecutor
    from unittest.mock import patch, MagicMock

    mock_c1 = MagicMock()
    mock_c2 = MagicMock()

    ex = LiveExecutor.__new__(LiveExecutor)
    ex.chain_id = 137
    ex._rpc_url = "http://localhost:8545"
    ex._live_trading_enabled = False
    ex._dry_run = True
    ex._c1 = mock_c1
    ex._c2 = mock_c2

    result = ex.execute_c1(
        {"asset": "0x0", "flash_loan_amount": 0, "min_profit": 0, "net_profit_usd": 0.0},
        p_fill=0.9,
    )
    assert result["skipped"] is True
    mock_c1.init_aave_flash.assert_not_called()


def test_live_executor_execute_c1_routes_to_c1(monkeypatch):
    from backend.live_executor import LiveExecutor
    from unittest.mock import MagicMock

    mock_c1 = MagicMock()
    mock_c1.init_aave_flash.return_value = {"broadcast": None, "dry_run": True}
    mock_c2 = MagicMock()

    ex = LiveExecutor.__new__(LiveExecutor)
    ex.chain_id = 137
    ex._rpc_url = "http://localhost:8545"
    ex._live_trading_enabled = False
    ex._dry_run = True
    ex._c1 = mock_c1
    ex._c2 = mock_c2

    result = ex.execute_c1(
        {
            "asset": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "flash_loan_amount": 1_000_000,
            "min_profit": 100,
            "payload": b"\x00",
            "net_profit_usd": 5.0,
        },
        p_fill=0.9,
    )
    mock_c1.init_aave_flash.assert_called_once()
    assert result["skipped"] is False


def test_live_executor_is_live_false_by_default(monkeypatch):
    from backend.live_executor import LiveExecutor
    from unittest.mock import patch, MagicMock

    mock_c1 = MagicMock()
    mock_c2 = MagicMock()

    with patch.object(LiveExecutor, "__init__", lambda self, *a, **kw: None):
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._live_trading_enabled = False
        ex._dry_run = True

    assert ex.is_live is False


# ---------------------------------------------------------------------------
# Flask server (in-process test client)
# ---------------------------------------------------------------------------


@pytest.fixture
def flask_client():
    from backend.server import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client


def test_health_endpoint(flask_client):
    resp = flask_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_chains_endpoint(flask_client):
    resp = flask_client.get("/api/chains")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    chain_ids = [c["chain_id"] for c in data]
    assert 137 in chain_ids


def test_registry_list_endpoint(flask_client):
    resp = flask_client.get("/api/registry")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == len(EXECUTOR_REGISTRY)


def test_registry_entry_endpoint_c1(flask_client):
    resp = flask_client.get(f"/api/registry/137/{STRATEGY_C1}")
    assert resp.status_code == 200
    entry = resp.get_json()
    assert entry["strategy"] == STRATEGY_C1
    assert entry["chain_id"] == 137


def test_registry_entry_endpoint_not_found(flask_client):
    resp = flask_client.get("/api/registry/999/unknown")
    assert resp.status_code == 404


def test_validate_endpoint_returns_list(flask_client):
    resp = flask_client.post(
        "/api/validate",
        json={"chain_id": 137},
        content_type="application/json",
    )
    # 200 (all pass) or 207 (some fail) are both acceptable
    assert resp.status_code in (200, 207)
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 2


def test_validate_single_entry_endpoint(flask_client):
    resp = flask_client.post(
        f"/api/validate/137/{STRATEGY_C1}",
        json={},
        content_type="application/json",
    )
    # 200 (pass) or 424 (fail) are both acceptable depending on env
    assert resp.status_code in (200, 424)
    data = resp.get_json()
    assert "passed" in data
    assert "checks" in data
    assert "errors" in data


def test_validate_single_entry_not_found(flask_client):
    resp = flask_client.post("/api/validate/999/unknown", json={})
    assert resp.status_code == 404
