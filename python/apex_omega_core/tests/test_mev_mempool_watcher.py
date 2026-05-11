"""Unit tests for MempoolWatcher (Feed D).

All tests run without a live WebSocket or RPC connection.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apex_omega_core.core.mev_mempool_watcher import (
    MempoolWatcher,
    MempoolStateSnapshot,
    PendingTx,
    _SWAP_SELECTORS,
    _make_mempool_state,
)


# ---------------------------------------------------------------------------
# PendingTx construction
# ---------------------------------------------------------------------------

class TestPendingTx:
    def test_fields_are_set(self):
        tx = PendingTx(
            tx_hash="0xabc",
            to="0xrouter",
            from_address="0xbot",
            value=0,
            gas=200_000,
            gas_price=50 * 10 ** 9,
            input_selector="0x38ed1739",
        )
        assert tx.tx_hash == "0xabc"
        assert tx.gas_price == 50 * 10 ** 9
        assert tx.input_selector == "0x38ed1739"

    def test_observed_at_is_recent(self):
        before = int(time.time() * 1_000)
        tx = PendingTx(
            tx_hash="0x1",
            to="0xrouter",
            from_address="0xsender",
            value=0,
            gas=100_000,
            gas_price=10 ** 9,
            input_selector="0x00000000",
        )
        after = int(time.time() * 1_000) + 1
        assert before <= tx.observed_at_ms <= after


# ---------------------------------------------------------------------------
# MempoolStateSnapshot helpers
# ---------------------------------------------------------------------------

class TestMempoolStateSnapshot:
    def test_pool_delta_defaults_to_zero(self):
        snap = MempoolStateSnapshot(
            snapshot_timestamp_ms=1000,
            pending_swap_count=0,
        )
        assert snap.pool_delta("0xabcd") == 0.0

    def test_pool_delta_returns_forecast(self):
        snap = MempoolStateSnapshot(
            snapshot_timestamp_ms=1000,
            pending_swap_count=1,
            reserve_delta_forecast={"0xrouter": 1.5},
        )
        assert snap.pool_delta("0xrouter") == pytest.approx(1.5)

    def test_make_mempool_state(self):
        txs = [
            PendingTx("0x1", "0xr", "0xf", 0, 1, 1, "0x00000000")
        ]
        snap = _make_mempool_state(txs, {"0xr": 2.0}, 0.1)
        assert snap.pending_swap_count == 1
        assert snap.reserve_delta_forecast["0xr"] == pytest.approx(2.0)
        assert snap.competing_bot_density == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# MempoolWatcher.get_state — pure in-memory logic
# ---------------------------------------------------------------------------

class TestMempoolWatcherGetState:
    def _watcher_with_txs(self, txs: list) -> MempoolWatcher:
        w = MempoolWatcher(wss_url="ws://fake", ttl_ms=5_000)
        for tx in txs:
            w._buffer.append(tx)
        return w

    def _fresh_tx(self, gas_price: int = 10 ** 9, selector: str = "0x00000000", to: str = "0xrouter", value: int = 0) -> PendingTx:
        return PendingTx(
            tx_hash="0xhash",
            to=to,
            from_address="0xsender",
            value=value,
            gas=200_000,
            gas_price=gas_price,
            input_selector=selector,
        )

    def test_empty_buffer_returns_zero_state(self):
        w = MempoolWatcher(wss_url="ws://fake")
        state = w.get_state()
        assert state.pending_swap_count == 0
        assert state.competing_bot_density == pytest.approx(0.0)

    def test_high_gas_tx_increments_bot_density(self):
        high_gas = 51 * 10 ** 9  # > 50 Gwei threshold
        txs = [self._fresh_tx(gas_price=high_gas) for _ in range(3)]
        w = self._watcher_with_txs(txs)
        state = w.get_state()
        assert state.competing_bot_density == pytest.approx(1.0)

    def test_low_gas_tx_does_not_count_as_bot(self):
        low_gas = 1 * 10 ** 9
        txs = [self._fresh_tx(gas_price=low_gas) for _ in range(4)]
        w = self._watcher_with_txs(txs)
        state = w.get_state()
        assert state.competing_bot_density == pytest.approx(0.0)

    def test_swap_selector_generates_reserve_delta(self):
        selector = "0x38ed1739"  # swapExactTokensForTokens
        assert selector in _SWAP_SELECTORS
        tx = self._fresh_tx(selector=selector, to="0xquickswap", value=int(1e18))
        w = self._watcher_with_txs([tx])
        state = w.get_state()
        assert "0xquickswap" in state.reserve_delta_forecast
        assert state.reserve_delta_forecast["0xquickswap"] == pytest.approx(1.0)

    def test_non_swap_selector_does_not_generate_delta(self):
        tx = self._fresh_tx(selector="0xdeadbeef", to="0xother", value=int(2e18))
        w = self._watcher_with_txs([tx])
        state = w.get_state()
        assert "0xother" not in state.reserve_delta_forecast

    def test_stale_txs_are_evicted(self):
        old_ms = int(time.time() * 1_000) - 10_000  # 10 s old
        old_tx = PendingTx(
            tx_hash="0xold",
            to="0xr",
            from_address="0xf",
            value=0,
            gas=100_000,
            gas_price=10 ** 9,
            input_selector="0x00000000",
            observed_at_ms=old_ms,
        )
        w = MempoolWatcher(wss_url="ws://fake", ttl_ms=5_000)
        w._buffer.append(old_tx)
        assert len(w._buffer) == 1
        state = w.get_state()
        assert state.pending_swap_count == 0  # evicted
        assert len(w._buffer) == 0

    def test_fresh_txs_are_not_evicted(self):
        w = MempoolWatcher(wss_url="ws://fake", ttl_ms=5_000)
        for _ in range(5):
            w._buffer.append(self._fresh_tx())
        state = w.get_state()
        assert state.pending_swap_count == 5

    def test_multiple_swaps_to_same_router_accumulate(self):
        selector = "0x38ed1739"
        txs = [
            self._fresh_tx(selector=selector, to="0xrouter", value=int(1e18)),
            self._fresh_tx(selector=selector, to="0xrouter", value=int(2e18)),
        ]
        w = self._watcher_with_txs(txs)
        state = w.get_state()
        assert state.reserve_delta_forecast["0xrouter"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# MempoolWatcher control flags
# ---------------------------------------------------------------------------

class TestMempoolWatcherControl:
    def test_is_connected_false_before_run(self):
        w = MempoolWatcher(wss_url="ws://fake")
        assert w.is_connected is False

    def test_stop_clears_running_flag(self):
        w = MempoolWatcher(wss_url="ws://fake")
        w._running = True
        w.stop()
        assert w._running is False

    def test_run_exits_immediately_when_no_wss_url(self):
        w = MempoolWatcher(wss_url="")
        asyncio.run(w.run())
        assert w.is_connected is False

    @pytest.mark.asyncio
    async def test_capture_snapshot_without_wss_returns_current_state(self):
        w = MempoolWatcher(wss_url="")
        state = await w.capture_snapshot(duration_s=0.1)
        assert state.pending_swap_count == 0

    @pytest.mark.asyncio
    async def test_capture_snapshot_rejects_non_positive_duration(self):
        w = MempoolWatcher(wss_url="ws://fake")
        with pytest.raises(ValueError):
            await w.capture_snapshot(duration_s=0)


# ---------------------------------------------------------------------------
# _fetch_tx — response parsing
# ---------------------------------------------------------------------------

class TestFetchTxParsing:
    @pytest.mark.asyncio
    async def test_returns_none_on_empty_result(self):
        w = MempoolWatcher(wss_url="ws://fake")
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"result": None})
        mock_session.post = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("os.environ", {"APEX_RPC_URL": "http://fake-rpc"}):
            result = await w._fetch_tx(mock_session, "0xdeadbeef")
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_tx_fields(self):
        w = MempoolWatcher(wss_url="ws://fake")
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={
            "result": {
                "hash": "0xabc123",
                "to": "0xRROUTER",
                "from": "0xSENDER",
                "value": "0xde0b6b3a7640000",  # 1 ETH in hex
                "gas": "0x30d40",
                "gasPrice": "0x77359400",  # 2 Gwei
                "input": "0x38ed17390000",
            }
        })
        mock_session.post = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("os.environ", {"APEX_RPC_URL": "http://fake-rpc"}):
            result = await w._fetch_tx(mock_session, "0xabc123")

        assert result is not None
        assert result.to == "0xrrouter"  # lowercased
        assert result.gas == 200_000
        assert result.input_selector == "0x38ed1739"
