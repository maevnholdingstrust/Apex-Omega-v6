"""Dashboard coordinator — async orchestration loop.

This module implements the backend orchestration loop that:

1. Runs the live scanner to collect venue quote rows.
2. Groups rows into token surfaces.
3. Builds and broadcasts scanner truth summaries (Layer A).
4. Detects when C1 recompute is needed and fires the recompute request.
5. Calls the C1 service and broadcasts C1 math truth (Layer B).

WebSocket event types emitted:

    scanner.venue_row          — one per token × venue (optional, raw)
    scanner.token_summary      — Layer A: best buy/sell, raw edge, status
    c1.recompute_requested     — C1 is about to run for this token
    c1.output                  — Layer B: optimal size, gross profit, min-outs
    c2.output                  — downstream (not orchestrated here)
    lane.job_update            — 32-lane core updates (not orchestrated here)
    execution.result           — on-chain execution result (not orchestrated here)
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .types import VenueQuoteRow, TokenMarketSurface, TokenSummaryRow, Pool
from .scanner_surface import (
    build_c1_intake,
    build_token_summary,
    group_rows_by_token,
    pool_to_venue_row,
    should_recompute,
)

logger = logging.getLogger(__name__)

_DEFAULT_SIZE_GRID_USD = [25_000.0, 50_000.0, 100_000.0]
_LOOP_INTERVAL_SECONDS = 0.25

# Type alias for an async broadcast callable: receives an event dict, returns None.
BroadcastFn = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class DashboardCoordinator:
    """Async orchestration loop: scanner → surface aggregation → dashboard.

    Parameters
    ----------
    scanner:
        Any object with a coroutine method
        ``scan_all_dexes(tokens: list[dict]) -> list[Pool]``
        that returns pool objects compatible with
        :func:`~apex_omega_core.core.scanner_surface.pool_to_venue_row`.
    ws_broadcast:
        Async callable that accepts a single event dict and delivers it to all
        connected dashboard WebSocket clients.
    c1_client:
        Any object with a coroutine method
        ``compute(intake: dict) -> dict``
        that accepts a C1 intake dict (matching the ``C1Intake`` Rust struct)
        and returns a C1 output dict (matching the ``C1Output`` Rust struct).
    size_grid_usd:
        Candidate notional sizes (USD) passed to the C1 intake builder.
    loop_interval_seconds:
        Sleep duration between scan cycles (default 250 ms).
    """

    def __init__(
        self,
        scanner: Any,
        ws_broadcast: BroadcastFn,
        c1_client: Any,
        size_grid_usd: Optional[List[float]] = None,
        loop_interval_seconds: float = _LOOP_INTERVAL_SECONDS,
    ) -> None:
        self.scanner = scanner
        self.ws_broadcast = ws_broadcast
        self.c1_client = c1_client
        self.size_grid_usd = size_grid_usd or _DEFAULT_SIZE_GRID_USD
        self.loop_interval_seconds = loop_interval_seconds

        # Per-token previous summary for change-detection / recompute gating.
        self._prev_summary_by_token: Dict[str, TokenSummaryRow] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, tokens: List[Dict[str, Any]]) -> None:
        """Run the orchestration loop indefinitely.

        Each iteration:
        1. Scans all DEXes for the supplied token list.
        2. Groups raw pool data into per-token surfaces.
        3. For each token surface:
           a. Builds and broadcasts a ``scanner.token_summary`` event.
           b. If a recompute is needed and the scanner status is CANDIDATE,
              builds a C1 intake, broadcasts ``c1.recompute_requested``,
              calls the C1 service, and broadcasts ``c1.output``.
        4. Sleeps for ``loop_interval_seconds`` before the next cycle.
        """
        while True:
            try:
                await self._run_single_cycle(tokens)
            except Exception:
                logger.exception("Error in DashboardCoordinator cycle")
            await asyncio.sleep(self.loop_interval_seconds)

    async def run_once(self, tokens: List[Dict[str, Any]]) -> None:
        """Run exactly one scan cycle (useful for testing)."""
        await self._run_single_cycle(tokens)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _run_single_cycle(self, tokens: List[Dict[str, Any]]) -> None:
        pools = await self.scanner.scan_all_dexes(tokens)
        rows = [self._pool_to_venue_row(p) for p in pools]
        surfaces = group_rows_by_token(rows)

        for token_address, surface in surfaces.items():
            await self._process_surface(token_address, surface)

    async def _process_surface(
        self,
        token_address: str,
        surface: TokenMarketSurface,
    ) -> None:
        summary = build_token_summary(surface)

        await self.ws_broadcast({
            "type": "scanner.token_summary",
            "payload": {
                "token_address": summary.token_address,
                "token_symbol": summary.token_symbol,
                "best_buy_venue": summary.best_buy_venue,
                "best_buy_price": summary.best_buy_price,
                "best_sell_venue": summary.best_sell_venue,
                "best_sell_price": summary.best_sell_price,
                "raw_spread": summary.raw_spread,
                "raw_spread_bps": summary.raw_spread_bps,
                "scanner_status": summary.scanner_status,
            },
        })

        prev = self._prev_summary_by_token.get(token_address)
        if should_recompute(prev, summary) and summary.scanner_status == "CANDIDATE":
            await self._trigger_c1_recompute(surface, summary)

        self._prev_summary_by_token[token_address] = summary

    async def _trigger_c1_recompute(
        self,
        surface: TokenMarketSurface,
        summary: TokenSummaryRow,
    ) -> None:
        intake = build_c1_intake(surface, self.size_grid_usd)
        if intake is None:
            return

        await self.ws_broadcast({
            "type": "c1.recompute_requested",
            "payload": {
                "token_address": summary.token_address,
                "token_symbol": summary.token_symbol,
            },
        })

        try:
            c1_out = await self.c1_client.compute(intake)
        except Exception:
            logger.exception(
                "C1 compute failed for token %s", summary.token_address
            )
            return

        await self.ws_broadcast({
            "type": "c1.output",
            "payload": c1_out,
        })

    @staticmethod
    def _pool_to_venue_row(pool: Any) -> VenueQuoteRow:
        """Convert a scanner pool object to a VenueQuoteRow.

        Accepts either a :class:`~apex_omega_core.core.types.Pool` instance
        (legacy) or any object that already is a :class:`VenueQuoteRow`.
        """
        if isinstance(pool, VenueQuoteRow):
            return pool
        if isinstance(pool, Pool):
            return pool_to_venue_row(pool)
        # Duck-typed fallback: try to use pool attributes directly.
        meta: Dict[str, Any] = getattr(pool, "metadata", {}) or {}
        return VenueQuoteRow(
            token_address=getattr(pool, "token0", ""),
            token_symbol=meta.get("token_symbol", ""),
            venue=getattr(pool, "dex", ""),
            pool_address=getattr(pool, "address", ""),
            buy_price_executable=meta.get("buy_price", 0.0),
            sell_price_executable=meta.get("sell_price", 0.0),
            liquidity_usd=meta.get("liquidity_usd", getattr(pool, "tvl_usd", 0.0)),
            fee_bps=int(getattr(pool, "fee", 0.0) * 10_000),
            freshness_ms=meta.get("freshness_ms", 0),
            quote_confidence=meta.get("quote_confidence", "unknown"),
            block_number=meta.get("block_number"),
            source=meta.get("source", ""),
            updated_at_ms=meta.get("updated_at_ms", 0),
            metadata=meta,
        )
