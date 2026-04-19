"""Tests for scanner_surface aggregation, C1 intake building, and recompute trigger."""

from __future__ import annotations

import asyncio
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from apex_omega_core.core.types import (
    VenueQuoteRow,
    TokenMarketSurface,
    MarketExtrema,
    TokenSummaryRow,
    Pool,
)
from apex_omega_core.core.scanner_surface import (
    compute_market_extrema,
    build_token_summary,
    build_c1_intake,
    should_recompute,
    group_rows_by_token,
    pool_to_venue_row,
)
from apex_omega_core.core.dashboard_coordinator import DashboardCoordinator


# ── Fixtures ──────────────────────────────────────────────────────────────────

TOKEN_A = "0xTokenA"
WETH_SYMBOL = "WETH"

_HIGH = "high"
_UNKNOWN = "unknown"


def _make_row(
    venue: str,
    pool: str,
    buy_px: float,
    sell_px: float,
    confidence: str = _HIGH,
    freshness_ms: int = 10,
    updated_at_ms: int = 1_000,
) -> VenueQuoteRow:
    return VenueQuoteRow(
        token_address=TOKEN_A,
        token_symbol=WETH_SYMBOL,
        venue=venue,
        pool_address=pool,
        buy_price_executable=buy_px,
        sell_price_executable=sell_px,
        liquidity_usd=1_000_000.0,
        fee_bps=30,
        freshness_ms=freshness_ms,
        quote_confidence=confidence,
        block_number=100,
        source="quoter",
        updated_at_ms=updated_at_ms,
    )


def _surface(*rows: VenueQuoteRow) -> TokenMarketSurface:
    return TokenMarketSurface(
        token_address=TOKEN_A,
        token_symbol=WETH_SYMBOL,
        rows=list(rows),
    )


# ── compute_market_extrema ────────────────────────────────────────────────────

class TestComputeMarketExtrema:
    def test_no_rows_returns_none_extrema(self) -> None:
        result = compute_market_extrema(_surface())
        assert result.raw_spread is None
        assert result.raw_spread_bps is None
        assert result.best_buy_venue is None
        assert result.best_sell_venue is None

    def test_only_low_confidence_rows_returns_none(self) -> None:
        row = _make_row("uni", "0xPool1", 100.0, 105.0, confidence=_UNKNOWN)
        result = compute_market_extrema(_surface(row))
        assert result.raw_spread is None

    def test_zero_buy_price_excluded(self) -> None:
        row = _make_row("uni", "0xPool1", 0.0, 105.0)
        result = compute_market_extrema(_surface(row))
        assert result.raw_spread is None

    def test_selects_lowest_buy_and_highest_sell(self) -> None:
        row1 = _make_row("uni_v3", "0xPool1", 2498.90, 2491.77)
        row2 = _make_row("quick_v2", "0xPool2", 2500.12, 2504.21)
        row3 = _make_row("quick_v3", "0xPool3", 2499.31, 2492.10)

        result = compute_market_extrema(_surface(row1, row2, row3))

        assert result.best_buy_venue == "uni_v3"
        assert result.best_buy_price == pytest.approx(2498.90)
        assert result.best_sell_venue == "quick_v2"
        assert result.best_sell_price == pytest.approx(2504.21)

    def test_raw_spread_and_bps(self) -> None:
        row1 = _make_row("uni", "0xPool1", 100.0, 95.0)
        row2 = _make_row("quick", "0xPool2", 102.0, 106.0)

        result = compute_market_extrema(_surface(row1, row2))

        assert result.raw_spread == pytest.approx(6.0)  # 106 - 100
        assert result.raw_spread_bps == pytest.approx(600.0)  # (6/100)*10_000

    def test_negative_edge_allowed(self) -> None:
        row1 = _make_row("uni", "0xPool1", 105.0, 100.0)
        result = compute_market_extrema(_surface(row1))
        assert result.raw_spread == pytest.approx(-5.0)

    def test_negative_freshness_excluded(self) -> None:
        row = VenueQuoteRow(
            token_address=TOKEN_A,
            token_symbol=WETH_SYMBOL,
            venue="uni",
            pool_address="0xPool1",
            buy_price_executable=100.0,
            sell_price_executable=105.0,
            freshness_ms=-1,
            quote_confidence=_HIGH,
            updated_at_ms=1_000,
        )
        result = compute_market_extrema(_surface(row))
        assert result.raw_spread is None


# ── build_token_summary ───────────────────────────────────────────────────────

class TestBuildTokenSummary:
    def test_no_data_status(self) -> None:
        summary = build_token_summary(_surface())
        assert summary.scanner_status == "NO_DATA"

    def test_no_edge_status(self) -> None:
        row = _make_row("uni", "0xPool1", 105.0, 100.0)
        summary = build_token_summary(_surface(row))
        assert summary.scanner_status == "NO_EDGE"

    def test_candidate_status(self) -> None:
        row = _make_row("uni", "0xPool1", 100.0, 106.0)
        summary = build_token_summary(_surface(row))
        assert summary.scanner_status == "CANDIDATE"

    def test_fields_populated(self) -> None:
        row1 = _make_row("uni_v3", "0xPool1", 2498.90, 2491.77, updated_at_ms=900)
        row2 = _make_row("quick_v2", "0xPool2", 2500.12, 2504.21, updated_at_ms=1_000)

        summary = build_token_summary(_surface(row1, row2))

        assert summary.token_address == TOKEN_A
        assert summary.token_symbol == WETH_SYMBOL
        assert summary.best_buy_venue == "uni_v3"
        assert summary.best_sell_venue == "quick_v2"
        assert summary.raw_spread == pytest.approx(5.31)
        assert summary.raw_spread_bps is not None


# ── build_c1_intake ───────────────────────────────────────────────────────────

class TestBuildC1Intake:
    def test_returns_none_when_no_data(self) -> None:
        result = build_c1_intake(_surface(), [50_000.0])
        assert result is None

    def test_returns_none_when_no_edge(self) -> None:
        row = _make_row("uni", "0xPool1", 105.0, 100.0)
        result = build_c1_intake(_surface(row), [50_000.0])
        assert result is None

    def test_returns_intake_dict_for_candidate(self) -> None:
        row1 = _make_row("uni_v3", "0xPool1", 2498.90, 2491.77, updated_at_ms=900)
        row2 = _make_row("quick_v2", "0xPool2", 2500.12, 2504.21, updated_at_ms=1_000)

        result = build_c1_intake(_surface(row1, row2), [25_000.0, 50_000.0])

        assert result is not None
        assert result["token_address"] == TOKEN_A
        assert result["token_symbol"] == WETH_SYMBOL
        assert result["buy_pool"]["venue"] == "uni_v3"
        assert result["sell_pool"]["venue"] == "quick_v2"
        assert result["size_grid_usd"] == [25_000.0, 50_000.0]
        assert result["observed_at_ms"] == 1_000
        assert result["raw_spread"] == pytest.approx(5.31)

    def test_observed_at_ms_is_max_updated_at(self) -> None:
        row1 = _make_row("uni", "0xPool1", 100.0, 95.0, updated_at_ms=500)
        row2 = _make_row("quick", "0xPool2", 102.0, 108.0, updated_at_ms=750)
        result = build_c1_intake(_surface(row1, row2), [50_000.0])
        assert result is not None
        assert result["observed_at_ms"] == 750


# ── should_recompute ──────────────────────────────────────────────────────────

def _summary(
    buy_venue: Optional[str] = "uni",
    sell_venue: Optional[str] = "quick",
    buy_px: Optional[float] = 100.0,
    sell_px: Optional[float] = 106.0,
    status: str = "CANDIDATE",
) -> TokenSummaryRow:
    return TokenSummaryRow(
        token_address=TOKEN_A,
        token_symbol=WETH_SYMBOL,
        best_buy_venue=buy_venue,
        best_buy_price=buy_px,
        best_sell_venue=sell_venue,
        best_sell_price=sell_px,
        raw_spread=6.0,
        raw_spread_bps=600.0,
        scanner_status=status,
    )


class TestShouldRecompute:
    def test_returns_true_when_prev_is_none(self) -> None:
        assert should_recompute(None, _summary()) is True

    def test_returns_false_when_nothing_changed(self) -> None:
        s = _summary()
        assert should_recompute(s, _summary()) is False

    def test_buy_venue_change_triggers(self) -> None:
        prev = _summary(buy_venue="uni")
        curr = _summary(buy_venue="sushi")
        assert should_recompute(prev, curr) is True

    def test_sell_venue_change_triggers(self) -> None:
        prev = _summary(sell_venue="quick")
        curr = _summary(sell_venue="dfyn")
        assert should_recompute(prev, curr) is True

    def test_large_buy_price_change_triggers(self) -> None:
        prev = _summary(buy_px=100.0)
        curr = _summary(buy_px=100.5)  # > 0.0001 threshold
        assert should_recompute(prev, curr) is True

    def test_tiny_buy_price_change_does_not_trigger(self) -> None:
        prev = _summary(buy_px=100.0)
        curr = _summary(buy_px=100.00005)  # < 0.0001
        assert should_recompute(prev, curr) is False

    def test_status_change_triggers(self) -> None:
        prev = _summary(status="CANDIDATE")
        curr = _summary(status="NO_EDGE")
        assert should_recompute(prev, curr) is True

    def test_none_prices_do_not_raise(self) -> None:
        prev = _summary(buy_px=None, sell_px=None)
        curr = _summary(buy_px=None, sell_px=None)
        assert should_recompute(prev, curr) is False


# ── group_rows_by_token ───────────────────────────────────────────────────────

class TestGroupRowsByToken:
    def test_empty_input(self) -> None:
        assert group_rows_by_token([]) == {}

    def test_single_token_two_venues(self) -> None:
        row1 = _make_row("uni", "0xPool1", 100.0, 105.0)
        row2 = _make_row("quick", "0xPool2", 101.0, 106.0)
        surfaces = group_rows_by_token([row1, row2])
        assert TOKEN_A in surfaces
        assert len(surfaces[TOKEN_A].rows) == 2

    def test_two_tokens(self) -> None:
        row1 = _make_row("uni", "0xPool1", 100.0, 105.0)
        row2 = VenueQuoteRow(
            token_address="0xTokenB",
            token_symbol="DAI",
            venue="quick",
            pool_address="0xPool3",
            buy_price_executable=1.0,
            sell_price_executable=1.001,
            updated_at_ms=1_000,
        )
        surfaces = group_rows_by_token([row1, row2])
        assert TOKEN_A in surfaces
        assert "0xTokenB" in surfaces
        assert len(surfaces[TOKEN_A].rows) == 1
        assert len(surfaces["0xTokenB"].rows) == 1


# ── pool_to_venue_row ─────────────────────────────────────────────────────────

class TestPoolToVenueRow:
    def test_converts_pool_with_metadata(self) -> None:
        pool = Pool(
            address="0xPool1",
            dex="uni",
            token0=TOKEN_A,
            token1="0xUSDC",
            tvl_usd=1_000_000.0,
            fee=0.003,
        )
        pool.metadata = {  # type: ignore[attr-defined]
            "token_symbol": "WETH",
            "buy_price": 2500.0,
            "sell_price": 2505.0,
            "quote_confidence": "high",
            "freshness_ms": 20,
            "updated_at_ms": 9_000,
        }
        row = pool_to_venue_row(pool)
        assert row.token_symbol == "WETH"
        assert row.buy_price_executable == pytest.approx(2500.0)
        assert row.fee_bps == 30
        assert row.quote_confidence == "high"
        assert row.updated_at_ms == 9_000

    def test_converts_pool_without_metadata(self) -> None:
        pool = Pool(
            address="0xPool1",
            dex="uni",
            token0=TOKEN_A,
            token1="0xUSDC",
            tvl_usd=500_000.0,
            fee=0.0030,
        )
        row = pool_to_venue_row(pool)
        assert row.venue == "uni"
        assert row.pool_address == "0xPool1"
        assert row.fee_bps == 30
        assert row.buy_price_executable == 0.0


# ── DashboardCoordinator ──────────────────────────────────────────────────────

class TestDashboardCoordinator:
    """Smoke-test the coordinator using mock scanner and C1 client."""

    def _make_scanner(self, rows: List[VenueQuoteRow]) -> Any:
        scanner = MagicMock()
        scanner.scan_all_dexes = AsyncMock(return_value=rows)
        return scanner

    def _make_c1_client(self, output: Dict[str, Any]) -> Any:
        c1 = MagicMock()
        c1.compute = AsyncMock(return_value=output)
        return c1

    @pytest.mark.asyncio
    async def test_broadcasts_scanner_summary(self) -> None:
        row = _make_row("uni", "0xPool1", 100.0, 95.0)  # NO_EDGE
        scanner = self._make_scanner([row])
        c1 = self._make_c1_client({})
        events: List[Dict[str, Any]] = []

        async def broadcast(event: Dict[str, Any]) -> None:
            events.append(event)

        coord = DashboardCoordinator(scanner, broadcast, c1)
        await coord.run_once([{"address": TOKEN_A}])

        summary_events = [e for e in events if e["type"] == "scanner.token_summary"]
        assert len(summary_events) == 1
        assert summary_events[0]["payload"]["scanner_status"] == "NO_EDGE"

    @pytest.mark.asyncio
    async def test_triggers_c1_for_candidate(self) -> None:
        row1 = _make_row("uni_v3", "0xPool1", 100.0, 95.0, updated_at_ms=1_000)
        row2 = _make_row("quick_v2", "0xPool2", 102.0, 110.0, updated_at_ms=2_000)
        scanner = self._make_scanner([row1, row2])
        c1_output = {
            "token_address": TOKEN_A,
            "status": "DETERMINISTIC_PROFIT",
            "gross_profit_usd": 50.0,
        }
        c1 = self._make_c1_client(c1_output)
        events: List[Dict[str, Any]] = []

        async def broadcast(event: Dict[str, Any]) -> None:
            events.append(event)

        coord = DashboardCoordinator(scanner, broadcast, c1)
        await coord.run_once([{"address": TOKEN_A}])

        types = [e["type"] for e in events]
        assert "c1.recompute_requested" in types
        assert "c1.output" in types
        c1_out_event = next(e for e in events if e["type"] == "c1.output")
        assert c1_out_event["payload"]["status"] == "DETERMINISTIC_PROFIT"

    @pytest.mark.asyncio
    async def test_no_c1_call_when_no_edge(self) -> None:
        row = _make_row("uni", "0xPool1", 105.0, 100.0)
        scanner = self._make_scanner([row])
        c1 = self._make_c1_client({})
        events: List[Dict[str, Any]] = []

        async def broadcast(event: Dict[str, Any]) -> None:
            events.append(event)

        coord = DashboardCoordinator(scanner, broadcast, c1)
        await coord.run_once([{"address": TOKEN_A}])

        assert not any(e["type"] == "c1.recompute_requested" for e in events)
        c1.compute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_recompute_when_summary_unchanged(self) -> None:
        row1 = _make_row("uni_v3", "0xPool1", 100.0, 95.0, updated_at_ms=1_000)
        row2 = _make_row("quick_v2", "0xPool2", 102.0, 110.0, updated_at_ms=2_000)
        scanner = self._make_scanner([row1, row2])
        c1 = self._make_c1_client({"status": "DETERMINISTIC_PROFIT"})
        events: List[Dict[str, Any]] = []

        async def broadcast(event: Dict[str, Any]) -> None:
            events.append(event)

        coord = DashboardCoordinator(scanner, broadcast, c1)
        # First cycle — should trigger C1.
        await coord.run_once([{"address": TOKEN_A}])
        first_c1_calls = c1.compute.call_count

        # Second cycle — same data, should NOT trigger C1 again.
        await coord.run_once([{"address": TOKEN_A}])
        assert c1.compute.call_count == first_c1_calls
