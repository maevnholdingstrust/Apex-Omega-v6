"""mev_mempool_watcher.py — Live Feed D: WebSocket mempool subscriber.

Subscribes to ``eth_subscribe('newPendingTransactions')`` via the Polygon
WebSocket endpoint, fetches each pending transaction, and derives
per-pool reserve-delta forecasts to populate a live :class:`MempoolState`
for the C1/C2 intake pipeline.

Environment variables
---------------------
POLYGON_WSS_URL   – Primary WebSocket RPC URL (falls back to APEX_WSS_URL).
APEX_WSS_URL      – Secondary WebSocket RPC URL.
MEMPOOL_BUFFER    – Maximum pending transactions to buffer (default 200).
MEMPOOL_TTL_MS    – Maximum age (ms) before a buffered tx is evicted (default 5 000).
MEMPOOL_RECONNECT – Seconds to wait before reconnecting on disconnect (default 2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_WSS_URL: str = (
    os.getenv("POLYGON_WSS_URL")
    or os.getenv("APEX_WSS_URL")
    or ""
)
_BUFFER_SIZE: int = int(os.getenv("MEMPOOL_BUFFER", "200"))
_TTL_MS: int = int(os.getenv("MEMPOOL_TTL_MS", "5000"))
_RECONNECT_DELAY: float = float(os.getenv("MEMPOOL_RECONNECT", "2"))

# Well-known Polygon Uniswap V2 / QuickSwap router function selector for
# swap calls (``swapExactTokensForTokens`` = 0x38ed1739).  Only txs whose
# calldata starts with a recognised swap selector are forwarded to the
# reserve-delta estimator.
_SWAP_SELECTORS = {
    "0x38ed1739",  # swapExactTokensForTokens
    "0x8803dbee",  # swapTokensForExactTokens
    "0x7ff36ab5",  # swapExactETHForTokens
    "0x4a25d94a",  # swapTokensForExactETH
    "0x18cbafe5",  # swapExactTokensForETH
    "0x414bf389",  # Uniswap V3 exactInputSingle
    "0xc04b8d59",  # Uniswap V3 exactInput (multi-hop)
    "0xdb3e2198",  # QuickSwap exactInputSingle
}


# ---------------------------------------------------------------------------
# Pending transaction record
# ---------------------------------------------------------------------------

@dataclass
class PendingTx:
    """Minimal record for a single observed pending transaction."""
    tx_hash: str
    to: str
    from_address: str
    value: int
    gas: int
    gas_price: int           # effective gas price (wei)
    input_selector: str      # first 4 bytes of calldata (hex, no 0x)
    observed_at_ms: int = field(default_factory=lambda: int(time.time() * 1_000))


# ---------------------------------------------------------------------------
# MempoolState (mirrors types.MempoolState for callers that import from here)
# ---------------------------------------------------------------------------

def _make_mempool_state(
    pending_txs: List[PendingTx],
    reserve_delta_forecast: Dict[str, float],
    competing_bot_density: float,
) -> "MempoolStateSnapshot":
    """Build a lightweight snapshot for Feed D consumers."""
    return MempoolStateSnapshot(
        snapshot_timestamp_ms=int(time.time() * 1_000),
        pending_swap_count=len(pending_txs),
        reserve_delta_forecast=dict(reserve_delta_forecast),
        competing_bot_density=competing_bot_density,
        freshness_age_ms=0,
    )


@dataclass
class MempoolStateSnapshot:
    """Feed D snapshot — mirrors ``types.MempoolState`` without the circular import."""
    snapshot_timestamp_ms: int
    pending_swap_count: int
    reserve_delta_forecast: Dict[str, float] = field(default_factory=dict)
    competing_bot_density: float = 0.0
    freshness_age_ms: int = 0

    def pool_delta(self, pool_address: str) -> float:
        """Forecast reserve delta for *pool_address*, defaulting to 0.0."""
        return self.reserve_delta_forecast.get(pool_address, 0.0)

    def to_types_mempool_state(self):
        """Convert to ``types.MempoolState`` if the types module is available."""
        try:
            from apex_omega_core.core.types import MempoolState
            return MempoolState(
                snapshot_timestamp_ms=self.snapshot_timestamp_ms,
                pending_swap_count=self.pending_swap_count,
                reserve_delta_forecast=self.reserve_delta_forecast,
                competing_bot_density=self.competing_bot_density,
                freshness_age_ms=self.freshness_age_ms,
            )
        except ImportError:
            return self


# ---------------------------------------------------------------------------
# MempoolWatcher
# ---------------------------------------------------------------------------

class MempoolWatcher:
    """Live WebSocket subscriber for Polygon pending transactions (Feed D).

    Subscribes to ``eth_subscribe('newPendingTransactions')`` over the
    configured WebSocket RPC endpoint, buffers recent pending swap
    transactions in a ring buffer, and exposes a live
    :class:`MempoolStateSnapshot` via :meth:`get_state`.

    Usage
    -----
    ::

        watcher = MempoolWatcher()
        asyncio.create_task(watcher.run())   # starts background subscription
        ...
        state = watcher.get_state()          # Feed D snapshot for C1 intake

    The background task handles disconnections automatically using
    exponential-back-off reconnect logic (capped at 60 s).

    Parameters
    ----------
    wss_url:
        WebSocket RPC endpoint.  Defaults to ``POLYGON_WSS_URL`` / ``APEX_WSS_URL``.
    buffer_size:
        Maximum number of pending transactions to buffer.
    ttl_ms:
        Maximum age (ms) before a buffered tx is evicted.
    reconnect_delay:
        Base reconnect delay in seconds.  Doubles on consecutive failures,
        capped at 60 s.
    """

    def __init__(
        self,
        wss_url: Optional[str] = None,
        buffer_size: int = _BUFFER_SIZE,
        ttl_ms: int = _TTL_MS,
        reconnect_delay: float = _RECONNECT_DELAY,
    ) -> None:
        self._wss_url: str = wss_url or _WSS_URL
        self._buffer: Deque[PendingTx] = deque(maxlen=buffer_size)
        self._ttl_ms: int = ttl_ms
        self._reconnect_delay: float = reconnect_delay
        self._running: bool = False
        self._connected: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> MempoolStateSnapshot:
        """Return a current Feed D snapshot based on the buffered pending txs.

        Stale transactions (age > ``ttl_ms``) are evicted before the snapshot
        is built so that callers always see fresh data.
        """
        self._evict_stale()
        txs = list(self._buffer)
        reserve_deltas: Dict[str, float] = {}
        bot_count: int = 0

        for tx in txs:
            # Count high-gas transactions as a proxy for competing bots.
            if tx.gas_price > 50 * 10 ** 9:  # > 50 Gwei = likely MEV bot
                bot_count += 1

            # Derive a simple reserve-delta forecast: we associate the
            # destination address (router) with a fractional delta.  A more
            # precise implementation would decode ABI calldata to extract the
            # exact pool address and amount; this conservative estimate keeps
            # the watcher self-contained.
            if tx.input_selector in _SWAP_SELECTORS and tx.to:
                existing = reserve_deltas.get(tx.to, 0.0)
                reserve_deltas[tx.to] = existing + float(tx.value or 0) * 1e-18

        density = bot_count / max(len(txs), 1) if txs else 0.0
        return _make_mempool_state(txs, reserve_deltas, density)

    @property
    def is_connected(self) -> bool:
        """True when the WebSocket subscription is currently active."""
        return self._connected

    async def run(self) -> None:
        """Start the subscription loop; runs until :meth:`stop` is called.

        This coroutine is designed to be wrapped in ``asyncio.create_task``.
        It automatically reconnects with exponential back-off on failure.
        """
        if not self._wss_url:
            logger.warning(
                "MempoolWatcher: no WebSocket URL configured "
                "(set POLYGON_WSS_URL or APEX_WSS_URL). Feed D will be empty."
            )
            return

        self._running = True
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._subscribe_loop()
                delay = self._reconnect_delay  # reset on clean disconnect
            except Exception as exc:
                logger.warning(
                    "MempoolWatcher: connection lost (%s). Reconnecting in %.1f s.",
                    exc,
                    delay,
                )
            finally:
                self._connected = False

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    def stop(self) -> None:
        """Signal the run loop to exit after the current reconnect cycle."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _subscribe_loop(self) -> None:
        """Open a WebSocket connection and process the newPendingTransactions feed."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self._wss_url) as ws:
                self._connected = True
                logger.info("MempoolWatcher: connected to %s", self._wss_url)

                # Subscribe to pending transaction hashes.
                await ws.send_json({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"],
                })

                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data, session)
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSE,
                    ):
                        break

    async def _handle_message(self, raw: str, session: Any) -> None:
        """Parse a WebSocket message and buffer the pending transaction."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Subscription response (id=1 with result = subscription id).
        if "id" in data and "result" in data:
            logger.debug("MempoolWatcher: subscription id = %s", data["result"])
            return

        # Subscription event.
        params = data.get("params") or {}
        result = params.get("result")
        if not result:
            return

        if isinstance(result, str):
            # Result is a tx hash — fetch the full transaction.
            tx_hash = result
            pending_tx = await self._fetch_tx(session, tx_hash)
            if pending_tx is not None:
                self._buffer.append(pending_tx)

    async def _fetch_tx(
        self, session: Any, tx_hash: str
    ) -> Optional[PendingTx]:
        """Fetch a transaction by hash and convert it to :class:`PendingTx`."""
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=5)
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash],
            }
            # Use HTTP RPC for fetches (WebSocket connection reserved for events).
            http_url = (
                os.getenv("APEX_RPC_URL")
                or os.getenv("POLYGON_RPC")
                or "https://polygon-rpc.com/"
            )
            async with session.post(
                http_url, json=payload, timeout=timeout
            ) as resp:
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("MempoolWatcher: failed to fetch tx %s: %s", tx_hash, exc)
            return None

        tx = (data.get("result") or {})
        if not tx:
            return None

        raw_input = tx.get("input") or "0x"
        selector = raw_input[2:10] if len(raw_input) >= 10 else ""

        return PendingTx(
            tx_hash=tx_hash,
            to=(tx.get("to") or "").lower(),
            from_address=(tx.get("from") or "").lower(),
            value=int(tx.get("value", "0x0"), 16),
            gas=int(tx.get("gas", "0x0"), 16),
            gas_price=int(
                tx.get("gasPrice") or tx.get("maxFeePerGas") or "0x0", 16
            ),
            input_selector=f"0x{selector}",
        )

    def _evict_stale(self) -> None:
        """Remove transactions older than ``_ttl_ms`` from the buffer."""
        now_ms = int(time.time() * 1_000)
        while self._buffer and (now_ms - self._buffer[0].observed_at_ms) > self._ttl_ms:
            self._buffer.popleft()
