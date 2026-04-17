"""Tests for mev_bundle: BundleTransaction, MEVBundle, BundleBuilder, BundleSimulator, BundleSubmitter."""

import asyncio
import json
import pytest

from apex_omega_core.core.mev_bundle import (
    BundleBuilder,
    BundleSimulator,
    BundleSubmitter,
    BundleTransaction,
    MEVBundle,
)


# ---------------------------------------------------------------------------
# BundleTransaction
# ---------------------------------------------------------------------------

def test_bundle_transaction_defaults() -> None:
    tx = BundleTransaction(signed_raw_tx="0xdeadbeef")
    assert tx.signed_raw_tx == "0xdeadbeef"
    assert tx.calldata == ""
    assert tx.expected_gas == 350_000


def test_bundle_transaction_with_calldata() -> None:
    tx = BundleTransaction(
        signed_raw_tx="0xabc",
        calldata="0x12345678",
        expected_gas=500_000,
    )
    assert tx.calldata == "0x12345678"
    assert tx.expected_gas == 500_000


# ---------------------------------------------------------------------------
# MEVBundle
# ---------------------------------------------------------------------------

def test_mev_bundle_defaults() -> None:
    tx = BundleTransaction(signed_raw_tx="0x00")
    bundle = MEVBundle(txs=[tx], target_block=99_000_000)
    assert bundle.target_block == 99_000_000
    assert bundle.min_profit_wei == 0
    assert bundle.simulation_id == ""
    assert bundle.submission_id == ""
    assert len(bundle.txs) == 1


def test_mev_bundle_multiple_txs() -> None:
    txs = [BundleTransaction(signed_raw_tx=f"0x{i:02x}") for i in range(3)]
    bundle = MEVBundle(txs=txs, target_block=1, min_profit_wei=1_000_000)
    assert len(bundle.txs) == 3
    assert bundle.min_profit_wei == 1_000_000


# ---------------------------------------------------------------------------
# BundleBuilder – unsigned / no-key path
# ---------------------------------------------------------------------------

class TestBundleBuilderNoKey:
    """Tests for BundleBuilder when no private key is available."""

    def _builder(self) -> BundleBuilder:
        from unittest.mock import MagicMock
        builder = BundleBuilder.__new__(BundleBuilder)
        builder.private_key = None
        builder.account = None
        # Minimal w3 mock so assemble() can call block_number.
        w3 = MagicMock()
        w3.eth.block_number = 100
        builder.w3 = w3
        return builder

    def test_build_signed_tx_returns_none_without_key(self) -> None:
        builder = self._builder()
        result = builder.build_signed_tx(
            to="0x1111111111111111111111111111111111111111",
            calldata="0xabcd",
            gas=300_000,
            max_fee_per_gas=int(100e9),
            max_priority_fee_per_gas=int(2e9),
        )
        assert result is None

    def test_assemble_returns_none_without_key(self) -> None:
        builder = self._builder()
        bundle = builder.assemble(
            calldata="0xabcd",
            target_address="0x1111111111111111111111111111111111111111",
            gas=300_000,
            max_fee_per_gas=int(100e9),
            max_priority_fee_per_gas=int(2e9),
        )
        assert bundle is None


# ---------------------------------------------------------------------------
# BundleSimulator – network mocked
# ---------------------------------------------------------------------------

class TestBundleSimulator:

    def _make_bundle(self) -> MEVBundle:
        return MEVBundle(
            txs=[BundleTransaction(signed_raw_tx="0x" + "ab" * 32)],
            target_block=50_000_000,
        )

    def test_simulate_returns_success_on_valid_response(self, aioresponses) -> None:
        import asyncio
        from aioresponses import aioresponses as mock_responses

        bundle = self._make_bundle()
        simulator = BundleSimulator(rpc_url="http://mock-rpc/")

        response_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "bundleHash": "0xhash123",
                "coinbaseDiff": "0x0",
                "results": [{"gasUsed": "0x5208"}],
            },
        }

        with mock_responses() as m:
            m.post("http://mock-rpc/", payload=response_body)
            result = asyncio.run(simulator.simulate(bundle))

        assert result["success"] is True
        assert result["simulated"] is True
        assert result["total_gas_used"] == 0x5208
        assert bundle.simulation_id == "0xhash123"

    def test_simulate_returns_failure_on_rpc_error(self, aioresponses) -> None:
        import asyncio
        from aioresponses import aioresponses as mock_responses

        bundle = self._make_bundle()
        simulator = BundleSimulator(rpc_url="http://mock-rpc/")

        response_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "execution reverted"},
        }

        with mock_responses() as m:
            m.post("http://mock-rpc/", payload=response_body)
            result = asyncio.run(simulator.simulate(bundle))

        assert result["success"] is False
        assert result["simulated"] is True

    def test_simulate_handles_network_failure(self) -> None:
        import asyncio
        bundle = self._make_bundle()
        # Unreachable host should be caught gracefully.
        simulator = BundleSimulator(rpc_url="http://127.0.0.1:9998/unreachable")
        result = asyncio.run(simulator.simulate(bundle))
        assert result["success"] is False
        assert result["simulated"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# BundleSubmitter – network mocked
# ---------------------------------------------------------------------------

class TestBundleSubmitter:

    def _make_bundle(self) -> MEVBundle:
        return MEVBundle(
            txs=[BundleTransaction(signed_raw_tx="0x" + "cd" * 32)],
            target_block=50_000_001,
        )

    def test_submit_returns_success_on_valid_relay_response(self, aioresponses) -> None:
        import asyncio
        from aioresponses import aioresponses as mock_responses

        bundle = self._make_bundle()
        submitter = BundleSubmitter(
            relay_url="http://mock-relay/",
            signing_key=None,
        )

        response_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"bundleHash": "0xbundle789"},
        }

        with mock_responses() as m:
            m.post("http://mock-relay/", payload=response_body)
            result = asyncio.run(submitter.submit(bundle))

        assert result["success"] is True
        assert result["bundle_hash"] == "0xbundle789"
        assert bundle.submission_id == "0xbundle789"

    def test_submit_handles_relay_error_field(self, aioresponses) -> None:
        import asyncio
        from aioresponses import aioresponses as mock_responses

        bundle = self._make_bundle()
        submitter = BundleSubmitter(relay_url="http://mock-relay/", signing_key=None)

        response_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "invalid request"},
        }

        with mock_responses() as m:
            m.post("http://mock-relay/", payload=response_body)
            result = asyncio.run(submitter.submit(bundle))

        assert result["success"] is False

    def test_submit_handles_network_failure(self) -> None:
        import asyncio
        bundle = self._make_bundle()
        submitter = BundleSubmitter(relay_url="http://127.0.0.1:9997/unreachable")
        result = asyncio.run(submitter.submit(bundle))
        assert result["success"] is False
        assert "error" in result

    def test_sign_payload_returns_none_without_key(self) -> None:
        submitter = BundleSubmitter(signing_key=None)
        assert submitter._sign_payload("some body") is None


# ---------------------------------------------------------------------------
# conftest – register aioresponses fixture if available
# ---------------------------------------------------------------------------

try:
    from aioresponses import aioresponses as _aioresponses

    @pytest.fixture
    def aioresponses():
        """Yield the aioresponses context for HTTP mocking."""
        yield _aioresponses

except ImportError:
    # aioresponses not installed; skip network-dependent tests.
    @pytest.fixture
    def aioresponses():
        pytest.skip("aioresponses not installed")
        yield None
