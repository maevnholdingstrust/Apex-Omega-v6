"""Scanner surface aggregation and C1 intake bridge.

This module implements the locked pipeline:

    Live Scanner
      ↓ (VenueQuoteRow per token × venue)
    Token Surface Aggregator  (TokenMarketSurface)
      ↓
    Best Buy / Best Sell Selector  (MarketExtrema)
      ↓
    Raw Edge Calculator
      ↓
    C1 Intake Builder  (dict ready for C1 service)
      ↓
    Recompute trigger  (should_recompute)

Downstream, C1 uses the intake dict to run Master Math and emit a C1Output.
The dashboard then shows both scanner truth (TokenSummaryRow) and math truth
(C1Output) as separate layers.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .types import (
    Pool,
    TokenMarketSurface,
    TokenSummaryRow,
    VenueQuoteRow,
    MarketExtrema,
)


# ── Best Buy / Best Sell Selector ─────────────────────────────────────────────

def compute_market_extrema(surface: TokenMarketSurface) -> MarketExtrema:
    """Select the best buy and best sell rows from a token surface.

    Only rows with ``quote_confidence == "high"``, non-negative freshness, and
    strictly positive executable prices are considered valid.

    Returns a :class:`MarketExtrema` with all ``None`` fields when no valid
    rows exist.
    """
    valid_rows = [
        r for r in surface.rows
        if r.quote_confidence == "high"
        and r.freshness_ms >= 0
        and r.buy_price_executable > 0
        and r.sell_price_executable > 0
    ]

    if not valid_rows:
        return MarketExtrema(
            best_buy_venue=None,
            best_buy_pool=None,
            best_buy_price=None,
            best_sell_venue=None,
            best_sell_pool=None,
            best_sell_price=None,
            raw_spread=None,
            raw_spread_bps=None,
        )

    best_buy = min(valid_rows, key=lambda r: r.buy_price_executable)
    best_sell = max(valid_rows, key=lambda r: r.sell_price_executable)

    raw_spread = best_sell.sell_price_executable - best_buy.buy_price_executable
    raw_spread_bps = (
        (raw_spread / best_buy.buy_price_executable) * 10_000
        if best_buy.buy_price_executable > 0
        else None
    )

    return MarketExtrema(
        best_buy_venue=best_buy.venue,
        best_buy_pool=best_buy.pool_address,
        best_buy_price=best_buy.buy_price_executable,
        best_sell_venue=best_sell.venue,
        best_sell_pool=best_sell.pool_address,
        best_sell_price=best_sell.sell_price_executable,
        raw_spread=raw_spread,
        raw_spread_bps=raw_spread_bps,
    )


# ── Dashboard Layer A: Scanner Summary Row ────────────────────────────────────

def build_token_summary(surface: TokenMarketSurface) -> TokenSummaryRow:
    """Build the scanner truth row shown in Dashboard Layer A.

    ``scanner_status`` reflects raw edge only — no fees or sizing applied.
    """
    extrema = compute_market_extrema(surface)

    if extrema.raw_spread is None:
        status = "NO_DATA"
    elif extrema.raw_spread <= 0:
        status = "NO_EDGE"
    else:
        status = "CANDIDATE"

    return TokenSummaryRow(
        token_address=surface.token_address,
        token_symbol=surface.token_symbol,
        best_buy_venue=extrema.best_buy_venue,
        best_buy_price=extrema.best_buy_price,
        best_sell_venue=extrema.best_sell_venue,
        best_sell_price=extrema.best_sell_price,
        raw_spread=extrema.raw_spread,
        raw_spread_bps=extrema.raw_spread_bps,
        scanner_status=status,
    )


# ── C1 Intake Builder ─────────────────────────────────────────────────────────

def build_c1_intake(
    surface: TokenMarketSurface,
    size_grid_usd: List[float],
) -> Optional[Dict[str, Any]]:
    """Convert scanner surface into the canonical C1 intake dict.

    Returns ``None`` when the surface has no positive edge — C1 should only
    be invoked when there is a candidate to evaluate.

    The returned dict matches the ``C1Intake`` Rust struct layout so it can be
    serialised directly as JSON and consumed by the C1 service.

    C1 must **not** trust ``raw_spread`` / ``raw_spread_bps`` from this dict
    as authoritative — it uses the scanner only to choose the candidate venue
    pair and then recomputes everything from the pool snapshots.
    """
    extrema = compute_market_extrema(surface)
    if extrema.raw_spread is None or extrema.raw_spread <= 0:
        return None

    buy_row = next(
        r for r in surface.rows
        if r.venue == extrema.best_buy_venue
        and r.pool_address == extrema.best_buy_pool
    )
    sell_row = next(
        r for r in surface.rows
        if r.venue == extrema.best_sell_venue
        and r.pool_address == extrema.best_sell_pool
    )

    def _row_to_pool_snapshot(row: VenueQuoteRow) -> Dict[str, Any]:
        return {
            "venue": row.venue,
            "pool_address": row.pool_address,
            "token_address": row.token_address,
            "quote_token_address": row.metadata.get("quote_token_address", ""),
            "buy_price_executable": row.buy_price_executable,
            "sell_price_executable": row.sell_price_executable,
            "liquidity_usd": row.liquidity_usd,
            "fee_bps": row.fee_bps,
            "freshness_ms": row.freshness_ms,
            "quote_confidence": row.quote_confidence,
            "block_number": row.block_number,
            "source": row.source,
        }

    observed_at_ms = max(r.updated_at_ms for r in surface.rows)
    block_number = buy_row.block_number or sell_row.block_number

    return {
        "token_address": surface.token_address,
        "token_symbol": surface.token_symbol,
        "buy_pool": _row_to_pool_snapshot(buy_row),
        "sell_pool": _row_to_pool_snapshot(sell_row),
        "raw_spread": extrema.raw_spread,
        "raw_spread_bps": extrema.raw_spread_bps,
        "size_grid_usd": size_grid_usd,
        "observed_at_ms": observed_at_ms,
        "block_number": block_number,
    }


# ── Real-Time Recompute Trigger ───────────────────────────────────────────────

_PRICE_CHANGE_THRESHOLD = 0.0001


def should_recompute(
    prev: Optional[TokenSummaryRow],
    curr: TokenSummaryRow,
) -> bool:
    """Return ``True`` when C1 should be triggered for a fresh recompute.

    Recompute is triggered when:

    * there is no previous summary (first observation)
    * the best buy or sell venue changed
    * the best buy or sell price moved beyond the threshold
    * the scanner status changed
    """
    if prev is None:
        return True

    if prev.best_buy_venue != curr.best_buy_venue:
        return True
    if prev.best_sell_venue != curr.best_sell_venue:
        return True

    if prev.best_buy_price is not None and curr.best_buy_price is not None:
        if abs(prev.best_buy_price - curr.best_buy_price) > _PRICE_CHANGE_THRESHOLD:
            return True

    if prev.best_sell_price is not None and curr.best_sell_price is not None:
        if abs(prev.best_sell_price - curr.best_sell_price) > _PRICE_CHANGE_THRESHOLD:
            return True

    if prev.scanner_status != curr.scanner_status:
        return True

    return False


# ── Pool-to-VenueQuoteRow Helper ──────────────────────────────────────────────

def pool_to_venue_row(pool: Pool) -> VenueQuoteRow:
    """Convert a legacy :class:`~apex_omega_core.core.types.Pool` to a
    :class:`VenueQuoteRow`.

    This is a compatibility shim for callers that still work with the
    ``Pool`` dataclass.  New code should produce ``VenueQuoteRow`` directly.
    """
    meta: Dict[str, Any] = getattr(pool, "metadata", {}) or {}
    return VenueQuoteRow(
        token_address=pool.token0,
        token_symbol=meta.get("token_symbol", ""),
        venue=pool.dex,
        pool_address=pool.address,
        buy_price_executable=meta.get("buy_price", 0.0),
        sell_price_executable=meta.get("sell_price", 0.0),
        liquidity_usd=meta.get("liquidity_usd", pool.tvl_usd),
        fee_bps=int(pool.fee * 10_000),
        freshness_ms=meta.get("freshness_ms", 0),
        quote_confidence=meta.get("quote_confidence", "unknown"),
        block_number=meta.get("block_number"),
        source=meta.get("source", ""),
        updated_at_ms=meta.get("updated_at_ms", 0),
        metadata=meta,
    )


def group_rows_by_token(
    rows: List[VenueQuoteRow],
) -> Dict[str, TokenMarketSurface]:
    """Group a flat list of venue rows into per-token surfaces."""
    grouped: Dict[str, List[VenueQuoteRow]] = defaultdict(list)
    for row in rows:
        grouped[row.token_address].append(row)

    return {
        token_address: TokenMarketSurface(
            token_address=token_address,
            token_symbol=token_rows[0].token_symbol,
            rows=token_rows,
        )
        for token_address, token_rows in grouped.items()
    }
