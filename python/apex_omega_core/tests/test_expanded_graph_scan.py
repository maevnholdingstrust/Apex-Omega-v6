"""Tests for apex_omega_core.core.expanded_graph_scan.

Unit tests (default) are fully offline — no RPC.
Live tests are gated behind @pytest.mark.live and only run when
POLYGON_RPC / POLYGON_HTTP is configured.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from apex_omega_core.core.expanded_graph_scan import (
    ExpandedGraphScanResult,
    ScanCandidate,
    ScoredRoute,
    _apply_gate,
    _canonical_cycle_key,
    _route_id,
    _route_label,
    expanded_graph_scan,
)
from apex_omega_core.core.route_graph import CycleRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(
    sym0: str,
    sym1: str,
    reserve0: float,
    reserve1: float,
    fee: float = 0.001,
    dex: str = "test",
    pool_address: str = "",
    kind: str = "cpmm",
    amp: float = 0.0,
) -> types.SimpleNamespace:
    addr = pool_address or f"0x_{sym0}_{sym1}_{dex}"
    price = (reserve1 / reserve0) if reserve0 > 0 else 0.0
    return types.SimpleNamespace(
        sym0=sym0, sym1=sym1, reserve0=reserve0, reserve1=reserve1,
        fee=fee, dex=dex, pool_address=addr, kind=kind, amp=amp, price=price,
    )


def _make_pool_map(*pools) -> dict:
    pm: dict = {}
    for p in pools:
        key = f"{p.sym0}/{p.sym1}"
        pm.setdefault(key, []).append(p)
    return pm


def _make_tip_optimizer(gas: float = 0.01, p_fill: float = 0.8) -> MagicMock:
    opt = MagicMock()
    opt.build_eip1559_params.return_value = {
        "gas_cost_usd": gas,
        "p_fill": p_fill,
        "expected_profit_usd": 5.0,
        "tip_gwei": 2.0,
        "base_fee_gwei": 50.0,
        "maxPriorityFeePerGas": 2_000_000_000,
        "maxFeePerGas": 102_000_000_000,
    }
    return opt


def _make_cycle_record(
    tokens: list = None,
    net: float = 10.0,
    p_fill: float = 0.8,
    hop_count: int = 2,
) -> CycleRecord:
    tokens = tokens or ["A", "B", "A"]
    e = net * p_fill if net > 0 else 0.0
    return CycleRecord(
        tokens=tokens,
        pools=[f"0xP{i}" for i in range(hop_count)],
        dexes=[f"dex{i}" for i in range(hop_count)],
        hop_count=hop_count,
        amount_in=1000.0,
        trade_size_usd=1000.0,
        amount_out=1010.0,
        gross_profit=10.0,
        gross_profit_usd=net + 2.0,
        flash_fee_usd=1.0,
        gas_cost_usd=1.0,
        net_profit_usd=net,
        p_fill=p_fill,
        e_profit=e,
        profitable=(net >= 1.0),
    )


def _make_scored_route(
    tokens: list = None,
    net: float = 10.0,
    p_fill: float = 0.8,
    fork_safe: bool = False,
    hop_count: int = 2,
) -> ScoredRoute:
    cycle = _make_cycle_record(tokens=tokens, net=net, p_fill=p_fill, hop_count=hop_count)
    return ScoredRoute(
        cycle=cycle,
        route_id=_route_id(cycle),
        route_label=_route_label(cycle),
        fork_safe=fork_safe,
    )


# ---------------------------------------------------------------------------
# _route_id
# ---------------------------------------------------------------------------

class TestRouteId:

    def test_deterministic(self):
        cr = _make_cycle_record()
        assert _route_id(cr) == _route_id(cr)

    def test_starts_with_prefix(self):
        cr = _make_cycle_record()
        assert _route_id(cr).startswith("rg_")

    def test_different_tokens_different_id(self):
        cr1 = _make_cycle_record(tokens=["A", "B", "A"])
        cr2 = _make_cycle_record(tokens=["A", "C", "A"])
        # Different route → different id
        assert _route_id(cr1) != _route_id(cr2)


# ---------------------------------------------------------------------------
# _route_label
# ---------------------------------------------------------------------------

class TestRouteLabel:

    def test_label_format(self):
        cr = _make_cycle_record(tokens=["WMATIC", "USDC", "WMATIC"])
        label = _route_label(cr)
        assert label == "WMATIC→USDC→WMATIC"

    def test_three_hop_label(self):
        cr = _make_cycle_record(tokens=["A", "B", "C", "A"], hop_count=3)
        label = _route_label(cr)
        assert label == "A→B→C→A"


# ---------------------------------------------------------------------------
# _canonical_cycle_key
# ---------------------------------------------------------------------------

class TestCanonicalCycleKey:

    def test_same_tokens_different_order_same_key(self):
        key1 = _canonical_cycle_key(["A", "B", "C", "A"])
        key2 = _canonical_cycle_key(["A", "C", "B", "A"])
        assert key1 == key2

    def test_different_token_set_different_key(self):
        key1 = _canonical_cycle_key(["A", "B", "C", "A"])
        key2 = _canonical_cycle_key(["A", "B", "D", "A"])
        assert key1 != key2

    def test_two_hop_key(self):
        key = _canonical_cycle_key(["A", "B", "A"])
        # Two-hop A→B→A: interior is ["A","B"]; minimal rotation is ("A","B")
        assert key == ("A", "B")


# ---------------------------------------------------------------------------
# ScoredRoute properties
# ---------------------------------------------------------------------------

class TestScoredRoute:

    def test_hop_count_passthrough(self):
        sr = _make_scored_route(hop_count=3)
        assert sr.hop_count == 3

    def test_net_profit_passthrough(self):
        sr = _make_scored_route(net=42.0)
        assert sr.net_profit_usd == pytest.approx(42.0)

    def test_e_profit_passthrough(self):
        sr = _make_scored_route(net=10.0, p_fill=0.5)
        assert sr.e_profit == pytest.approx(5.0)

    def test_p_fill_passthrough(self):
        sr = _make_scored_route(p_fill=0.65)
        assert sr.p_fill == pytest.approx(0.65)

    def test_profitable_passthrough(self):
        sr = _make_scored_route(net=5.0)
        assert sr.profitable is True

    def test_profitable_false_when_net_low(self):
        sr = _make_scored_route(net=0.0)
        assert sr.profitable is False


# ---------------------------------------------------------------------------
# ScanCandidate / _apply_gate
# ---------------------------------------------------------------------------

class TestApplyGate:

    def test_fork_safe_blocks_execution(self):
        sr = _make_scored_route(net=100.0, p_fill=0.9, fork_safe=True)
        candidate = _apply_gate(sr, fork_safe=True, min_p_fill=0.1)
        assert candidate.execution_ready is False
        assert "fork_safe" in candidate.gate_reason

    def test_unprofitable_blocked(self):
        sr = _make_scored_route(net=-1.0, fork_safe=False)
        candidate = _apply_gate(sr, fork_safe=False, min_p_fill=0.1)
        assert candidate.execution_ready is False
        assert "unprofitable" in candidate.gate_reason.lower() or "profit" in candidate.gate_reason.lower()

    def test_low_p_fill_blocked(self):
        sr = _make_scored_route(net=10.0, p_fill=0.05, fork_safe=False)
        candidate = _apply_gate(sr, fork_safe=False, min_p_fill=0.2)
        assert candidate.execution_ready is False
        assert "p_fill" in candidate.gate_reason

    def test_all_conditions_pass(self):
        sr = _make_scored_route(net=20.0, p_fill=0.9, fork_safe=False)
        candidate = _apply_gate(sr, fork_safe=False, min_p_fill=0.1)
        assert candidate.execution_ready is True
        assert candidate.gate_reason == "ok"

    def test_zero_net_profit_blocked(self):
        sr = _make_scored_route(net=0.0, fork_safe=False)
        candidate = _apply_gate(sr, fork_safe=False, min_p_fill=0.0)
        assert candidate.execution_ready is False


# ---------------------------------------------------------------------------
# ExpandedGraphScanResult structure
# ---------------------------------------------------------------------------

class TestExpandedGraphScanResult:

    def _run(self, **kwargs) -> ExpandedGraphScanResult:
        p_cheap = _make_pool("A", "B", 1_000_000.0, 2_000_000.0, fee=0.001,
                             dex="dex1", pool_address="0xCHEAP")
        p_dear = _make_pool("A", "B", 2_000_000.0, 1_000_000.0, fee=0.001,
                            dex="dex2", pool_address="0xDEAR")
        pm = _make_pool_map(p_cheap, p_dear)
        opt = _make_tip_optimizer(gas=0.01, p_fill=0.8)
        prices = {"A": 1.0, "B": 2.0}
        defaults: dict = dict(
            pool_map=pm,
            token_prices=prices,
            tip_optimizer=opt,
            min_hops=2,
            max_hops=2,
            min_net_profit_usd=0.01,
            max_trade_size_usd=100_000.0,
        )
        defaults.update(kwargs)
        return expanded_graph_scan(**defaults)

    def test_returns_expanded_graph_scan_result(self):
        result = self._run()
        assert isinstance(result, ExpandedGraphScanResult)

    def test_has_required_fields(self):
        result = self._run()
        assert hasattr(result, "scan_timestamp")
        assert hasattr(result, "candidates")
        assert hasattr(result, "total_cycles_evaluated")
        assert hasattr(result, "profitable_cycles")
        assert hasattr(result, "top_candidate")
        assert hasattr(result, "hop_range")
        assert hasattr(result, "elapsed_seconds")
        assert hasattr(result, "metadata")

    def test_elapsed_seconds_positive(self):
        result = self._run()
        assert result.elapsed_seconds >= 0.0

    def test_hop_range_correct(self):
        result = self._run(min_hops=2, max_hops=3)
        assert result.hop_range == (2, 3)

    def test_candidates_sorted_by_e_profit(self):
        result = self._run()
        e_profits = [c.scored_route.e_profit for c in result.candidates]
        assert e_profits == sorted(e_profits, reverse=True)

    def test_fork_safe_true_no_execution_ready(self):
        """With fork_safe=True (default), no candidate should be execution_ready."""
        result = self._run(fork_safe=True)
        for c in result.candidates:
            assert c.execution_ready is False

    def test_fork_safe_false_may_have_execution_ready(self):
        """With fork_safe=False, profitable candidates become execution_ready."""
        result = self._run(fork_safe=False, min_p_fill=0.01)
        # At least some candidates should be execution_ready if profitable
        any_ready = any(c.execution_ready for c in result.candidates)
        if result.profitable_cycles > 0:
            assert any_ready

    def test_empty_pool_map(self):
        opt = _make_tip_optimizer()
        result = expanded_graph_scan(
            pool_map={}, token_prices={}, tip_optimizer=opt
        )
        assert result.candidates == []
        assert result.total_cycles_evaluated == 0
        assert result.top_candidate is None

    def test_metadata_passthrough(self):
        meta = {"block_number": 12345, "scan_id": "test-001"}
        result = self._run(metadata=meta)
        assert result.metadata["block_number"] == 12345
        assert result.metadata["scan_id"] == "test-001"

    def test_deduplication_reduces_symmetric_cycles(self):
        result_dedup = self._run(deduplicate_symmetric=True)
        result_nodup = self._run(deduplicate_symmetric=False)
        # Deduplication should produce ≤ results than without
        assert len(result_dedup.candidates) <= len(result_nodup.candidates)

    def test_total_cycles_evaluated_ge_profitable_cycles(self):
        """total_cycles_evaluated must always be >= profitable_cycles."""
        result = self._run(min_net_profit_usd=0.01)
        assert result.total_cycles_evaluated >= result.profitable_cycles

    def test_max_hops_clamped_to_six(self):
        """max_hops > 6 should be silently clamped; no explosion or error."""
        result = self._run(max_hops=99)
        assert result.hop_range[1] <= 6


# ---------------------------------------------------------------------------
# Package-level export guard
# ---------------------------------------------------------------------------

class TestExpandedScanExports:

    def test_expanded_graph_scan_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "expanded_graph_scan")
        assert "expanded_graph_scan" in core_pkg.__all__

    def test_scored_route_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "ScoredRoute")
        assert "ScoredRoute" in core_pkg.__all__

    def test_scan_candidate_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "ScanCandidate")
        assert "ScanCandidate" in core_pkg.__all__

    def test_expanded_graph_scan_result_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "ExpandedGraphScanResult")
        assert "ExpandedGraphScanResult" in core_pkg.__all__


# ---------------------------------------------------------------------------
# Live tests — require a live Polygon RPC
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestExpandedScanLive:
    """Integration tests against the live Polygon network.

    These tests require:
      - A reachable Polygon RPC (POLYGON_RPC or POLYGON_HTTP env var)
      - Real pool discovery (takes ~5-10s)

    Run with: pytest -m live
    Skip with: pytest -m "not live"
    """

    @pytest.fixture(scope="class")
    def live_pool_map_and_prices(self):
        """Discover real pools from Polygon mainnet."""
        import asyncio
        import os
        from web3 import Web3

        rpc = os.getenv("POLYGON_RPC") or os.getenv("POLYGON_HTTP", "")
        if not rpc:
            pytest.skip("POLYGON_RPC not set; skipping live expanded scan test")

        try:
            from python.dry_run import _discover_pools, _derive_token_prices_usd, _filter_pool_universe
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if not w3.is_connected():
                pytest.skip(f"Cannot connect to Polygon RPC at {rpc!r}")
            pool_map = _discover_pools(w3, max_workers=8)
            token_prices = _derive_token_prices_usd(pool_map)
            pool_map = _filter_pool_universe(pool_map, token_prices)
            return pool_map, token_prices
        except Exception as exc:
            pytest.skip(f"Live pool discovery failed: {exc}")

    @pytest.fixture(scope="class")
    def live_tip_optimizer(self):
        from apex_omega_core.core.mev_gas_oracle import GasOracle, TipOptimizer
        import os
        rpc = os.getenv("POLYGON_RPC") or os.getenv("POLYGON_HTTP", "https://polygon-rpc.com/")
        try:
            oracle = GasOracle(rpc_url=rpc)
            snap = oracle.get_snapshot()
            return TipOptimizer(snap, gas_units=450_000, chain="polygon")
        except Exception as exc:
            pytest.skip(f"Could not build live TipOptimizer: {exc}")

    def test_live_scan_returns_result(self, live_pool_map_and_prices, live_tip_optimizer):
        pool_map, token_prices = live_pool_map_and_prices
        result = expanded_graph_scan(
            pool_map=pool_map,
            token_prices=token_prices,
            tip_optimizer=live_tip_optimizer,
            min_hops=2,
            max_hops=3,
            min_net_profit_usd=0.01,
            fork_safe=True,
        )
        assert isinstance(result, ExpandedGraphScanResult)
        assert result.elapsed_seconds > 0.0
        # fork_safe=True → no execution-ready candidates
        for c in result.candidates:
            assert c.execution_ready is False

    def test_live_scan_all_cycles_have_valid_token_sequence(
        self, live_pool_map_and_prices, live_tip_optimizer
    ):
        pool_map, token_prices = live_pool_map_and_prices
        result = expanded_graph_scan(
            pool_map=pool_map,
            token_prices=token_prices,
            tip_optimizer=live_tip_optimizer,
            min_hops=2,
            max_hops=3,
            min_net_profit_usd=0.01,
            fork_safe=True,
        )
        for c in result.candidates:
            toks = c.scored_route.cycle.tokens
            assert toks[0] == toks[-1], f"Cycle does not start == end: {toks}"
            assert len(toks) >= 3
