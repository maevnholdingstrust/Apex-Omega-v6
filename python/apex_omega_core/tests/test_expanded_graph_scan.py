"""Tests for expanded_graph_scan.py.

Unit tests (no RPC, no network) run unconditionally.
Live integration tests require a live Polygon RPC and are
gated with ``@pytest.mark.live`` — run with::

    pytest -m live

or skip them::

    pytest -m "not live"
"""

from __future__ import annotations

import os
import pytest

from apex_omega_core.core.expanded_graph_scan import (
    RouteHop,
    ScoredRoute,
    ForkReadiness,
    ScanCandidate,
    ExpandedGraphScanResult,
    _execution_grade,
    _check_fork_readiness,
)


# ---------------------------------------------------------------------------
# Unit tests — no RPC required
# ---------------------------------------------------------------------------

class TestRouteHop:
    def test_fields_stored_correctly(self):
        hop = RouteHop(
            pool_address="0xABC",
            dex="univ3_500",
            token_in="USDC",
            token_out="WETH",
            fee=0.0005,
            estimated_out=0.04,
        )
        assert hop.pool_address == "0xABC"
        assert hop.dex == "univ3_500"
        assert hop.token_in == "USDC"
        assert hop.token_out == "WETH"
        assert hop.fee == pytest.approx(0.0005)
        assert hop.estimated_out == pytest.approx(0.04)

    def test_default_estimated_out(self):
        hop = RouteHop(
            pool_address="0x1",
            dex="qsv2",
            token_in="A",
            token_out="B",
            fee=0.003,
        )
        assert hop.estimated_out == 0.0


class TestScoredRoute:
    def _make_route(self, net_profit_usd: float = 5.0) -> ScoredRoute:
        hops = [
            RouteHop("0x1", "univ3_500", "USDC", "WMATIC", 0.0005),
            RouteHop("0x2", "qsv2",      "WMATIC", "WETH",  0.003),
            RouteHop("0x3", "univ3_3000","WETH",   "USDC",  0.003),
        ]
        return ScoredRoute(
            hops=hops,
            input_token="USDC",
            input_amount=100.0,
            output_amount=105.0,
            net_profit=5.0,
            net_profit_usd=net_profit_usd,
            gross_profit_usd=net_profit_usd + 1.0,
            raw_spread_bps=50.0,
            execution_grade=_execution_grade(net_profit_usd),
            optimal_size_usd=100.0,
        )

    def test_hop_count_auto_computed(self):
        route = self._make_route()
        assert route.hop_count == 3

    def test_single_hop_count(self):
        route = ScoredRoute(
            hops=[RouteHop("0x1", "qsv2", "A", "B", 0.003)],
            input_token="A",
            input_amount=1.0,
            output_amount=1.01,
            net_profit=0.01,
            net_profit_usd=0.01,
            gross_profit_usd=0.02,
            raw_spread_bps=10.0,
            execution_grade="D",
            optimal_size_usd=1.0,
        )
        assert route.hop_count == 1

    def test_grade_a(self):
        route = self._make_route(net_profit_usd=15.0)
        assert route.execution_grade == "A"

    def test_grade_b(self):
        route = self._make_route(net_profit_usd=3.0)
        assert route.execution_grade == "B"

    def test_grade_c(self):
        route = self._make_route(net_profit_usd=0.75)
        assert route.execution_grade == "C"

    def test_grade_d(self):
        route = self._make_route(net_profit_usd=0.05)
        assert route.execution_grade == "D"


class TestExecutionGrade:
    @pytest.mark.parametrize("profit,expected", [
        (100.0,  "A"),
        (10.0,   "A"),
        (9.99,   "B"),
        (2.0,    "B"),
        (1.99,   "C"),
        (0.50,   "C"),
        (0.49,   "D"),
        (0.01,   "D"),
        (0.0,    "D"),
        (-1.0,   "D"),
    ])
    def test_grade_boundaries(self, profit, expected):
        assert _execution_grade(profit) == expected


class TestForkReadiness:
    def test_skipped_status(self):
        fr = ForkReadiness(status="SKIPPED")
        assert fr.status == "SKIPPED"
        assert fr.message == ""

    def test_ready_status(self):
        fr = ForkReadiness(status="READY")
        assert fr.status == "READY"

    def test_error_with_message(self):
        fr = ForkReadiness(status="ERROR", message="Connection refused")
        assert fr.status == "ERROR"
        assert "refused" in fr.message


class TestScanCandidate:
    def test_construction(self):
        hops = [RouteHop("0xA", "qsv2", "USDC", "WMATIC", 0.003)]
        route = ScoredRoute(
            hops=hops,
            input_token="USDC",
            input_amount=100.0,
            output_amount=103.0,
            net_profit=3.0,
            net_profit_usd=3.0,
            gross_profit_usd=4.0,
            raw_spread_bps=30.0,
            execution_grade="B",
            optimal_size_usd=100.0,
        )
        candidate = ScanCandidate(
            route=route,
            fork_readiness=ForkReadiness(status="SKIPPED"),
        )
        assert candidate.route.net_profit_usd == pytest.approx(3.0)
        assert candidate.fork_readiness.status == "SKIPPED"


class TestExpandedGraphScanResult:
    def test_construction(self):
        result = ExpandedGraphScanResult(
            scanned=50,
            candidates=[],
            rejected=49,
            scan_duration_sec=3.5,
            rpc_url="https://polygon-rpc.com/",
            token_count=14,
            pair_count=91,
        )
        assert result.scanned == 50
        assert result.candidates == []
        assert result.rejected == 49
        assert result.scan_duration_sec == pytest.approx(3.5)
        assert result.token_count == 14
        assert result.pair_count == 91

    def test_zero_candidates(self):
        result = ExpandedGraphScanResult(
            scanned=0,
            candidates=[],
            rejected=0,
            scan_duration_sec=0.1,
            rpc_url="http://localhost:8545",
            token_count=3,
            pair_count=3,
        )
        assert len(result.candidates) == 0


class TestCheckForkReadinessOffline:
    def test_unreachable_url_returns_error(self):
        """A URL that points nowhere must return ERROR, not raise."""
        result = _check_fork_readiness(
            fork_rpc_url="http://127.0.0.1:19999",  # nothing listening
            first_pool_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            timeout=0.5,
        )
        assert result.status in ("ERROR", "NOT_READY")

    def test_invalid_url_returns_error(self):
        result = _check_fork_readiness(
            fork_rpc_url="not-a-url",
            first_pool_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            timeout=0.5,
        )
        assert result.status == "ERROR"


# ---------------------------------------------------------------------------
# Live integration tests — require POLYGON_RPC
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestRunExpandedGraphScanLive:
    """Full end-to-end live scan.

    Run with::

        POLYGON_RPC=https://polygon-rpc.com/ pytest -m live -v
    """

    def test_scan_returns_result(self):
        from apex_omega_core.core.expanded_graph_scan import run_expanded_graph_scan

        result = run_expanded_graph_scan(
            fork_rpc_url=None,
            start_stable="USDCe",
            start_amount=100.0,
            max_hops=3,
            max_tokens=10,       # small universe for fast CI
            min_net_profit_usdc=0.01,
        )
        assert isinstance(result, ExpandedGraphScanResult)
        assert result.token_count >= 1
        assert result.pair_count >= 1
        assert result.scanned >= 0
        assert result.scan_duration_sec > 0.0
        # RPC URL should be non-empty
        assert len(result.rpc_url) > 0

    def test_candidates_sorted_by_profit(self):
        from apex_omega_core.core.expanded_graph_scan import run_expanded_graph_scan

        result = run_expanded_graph_scan(
            start_stable="USDCe",
            start_amount=100.0,
            max_hops=3,
            max_tokens=8,
            min_net_profit_usdc=0.01,
        )
        if len(result.candidates) >= 2:
            profits = [c.route.net_profit_usd for c in result.candidates]
            assert profits == sorted(profits, reverse=True)

    def test_candidate_structure(self):
        from apex_omega_core.core.expanded_graph_scan import run_expanded_graph_scan

        result = run_expanded_graph_scan(
            start_stable="USDCe",
            start_amount=50.0,
            max_hops=3,
            max_tokens=8,
            min_net_profit_usdc=0.001,
        )
        for candidate in result.candidates:
            assert isinstance(candidate, ScanCandidate)
            assert candidate.route.hop_count >= 2
            assert candidate.route.execution_grade in ("A", "B", "C", "D")
            assert candidate.fork_readiness.status == "SKIPPED"  # no fork_rpc_url
            assert candidate.route.net_profit_usd >= 0.001

    def test_no_rpc_raises_connection_error(self):
        from apex_omega_core.core.expanded_graph_scan import run_expanded_graph_scan

        with pytest.raises(ConnectionError):
            run_expanded_graph_scan(
                rpc_url="http://127.0.0.1:19999",  # nothing listening
                start_stable="USDCe",
                start_amount=100.0,
                max_hops=3,
                max_tokens=5,
                min_net_profit_usdc=0.01,
                _retry_delay=0,   # skip inter-attempt sleep so test is fast
            )
