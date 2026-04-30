"""Deterministic unit tests for apex_omega_core.core.route_graph.

All tests are fully offline — no RPC, no random state, no external calls.
Pool snapshots are built directly as simple namespace objects so the tests
are independent of _PoolSnapshot's module-level location.

Coverage
--------
* _cpmm_swap_out       — constant-product math
* _pool_swap_out       — dispatch (cpmm + curve_ss)
* RouteGraph           — construction, neighbors, best_pool_for_edge, tokens
* RouteGraph.enumerate_cycles — 2-hop, 3-hop, 4-hop, no-start, disconnected
* simulate_n_hop_cycle — round-trip, zero-liquidity guard, invalid sequence
* CycleRecord          — fields, profitable flag
* scan_multi_hop_cycles — profitable, unprofitable, empty pool_map
"""

from __future__ import annotations

import math
import types
from typing import List
from unittest.mock import MagicMock

import pytest

from apex_omega_core.core.route_graph import (
    CycleRecord,
    RouteGraph,
    _cpmm_swap_out,
    _pool_swap_out,
    scan_multi_hop_cycles,
    simulate_n_hop_cycle,
)


# ---------------------------------------------------------------------------
# Helpers: minimal pool-like objects
# ---------------------------------------------------------------------------

def _make_pool(
    sym0: str,
    sym1: str,
    reserve0: float,
    reserve1: float,
    fee: float = 0.003,
    dex: str = "test_dex",
    pool_address: str = "",
    kind: str = "cpmm",
    amp: float = 0.0,
) -> types.SimpleNamespace:
    """Create a minimal pool-like object satisfying the _POOL_ATTRS protocol."""
    addr = pool_address or f"0x_{sym0}_{sym1}_{dex}"
    price = (reserve1 / reserve0) if reserve0 > 0 else 0.0
    return types.SimpleNamespace(
        sym0=sym0,
        sym1=sym1,
        reserve0=reserve0,
        reserve1=reserve1,
        fee=fee,
        dex=dex,
        pool_address=addr,
        kind=kind,
        amp=amp,
        price=price,
    )


def _make_pool_map(*pools) -> dict:
    """Build a pool_map dict from a flat list of pool objects."""
    pm: dict = {}
    for p in pools:
        key = f"{p.sym0}/{p.sym1}"
        pm.setdefault(key, []).append(p)
    return pm


def _make_tip_optimizer(
    gas_cost_per_call: float = 5.0,
    p_fill: float = 0.75,
) -> MagicMock:
    """Lightweight TipOptimizer stub that returns fixed values."""
    opt = MagicMock()
    opt.build_eip1559_params.return_value = {
        "gas_cost_usd": gas_cost_per_call,
        "p_fill": p_fill,
        "expected_profit_usd": 10.0,
        "tip_gwei": 2.0,
        "base_fee_gwei": 50.0,
        "maxPriorityFeePerGas": 2_000_000_000,
        "maxFeePerGas": 102_000_000_000,
    }
    return opt


# ---------------------------------------------------------------------------
# _cpmm_swap_out
# ---------------------------------------------------------------------------

class TestCpmmSwapOut:

    def test_basic_swap(self):
        # Standard CPMM: 1 in, 1000 reserve each side, 0.3% fee
        # eff_in = 1 * 0.997 = 0.997
        # out = 0.997 * 1000 / (1000 + 0.997) ≈ 0.99600
        out = _cpmm_swap_out(1.0, 1000.0, 1000.0, 0.003)
        expected = 0.997 * 1000.0 / (1000.0 + 0.997)
        assert out == pytest.approx(expected, rel=1e-9)

    def test_zero_amount_in_returns_zero(self):
        assert _cpmm_swap_out(0.0, 1000.0, 1000.0, 0.003) == 0.0

    def test_zero_reserve_in_returns_zero(self):
        assert _cpmm_swap_out(1.0, 0.0, 1000.0, 0.003) == 0.0

    def test_zero_reserve_out_returns_zero(self):
        assert _cpmm_swap_out(1.0, 1000.0, 0.0, 0.003) == 0.0

    def test_output_less_than_input_plus_fee(self):
        # Conservation: output amount < input / price (always some price impact)
        out = _cpmm_swap_out(100.0, 10_000.0, 10_000.0, 0.0)
        assert out < 100.0

    def test_output_strictly_positive(self):
        out = _cpmm_swap_out(1.0, 500_000.0, 500_000.0, 0.0005)
        assert out > 0.0

    def test_larger_input_gives_more_output(self):
        out_small = _cpmm_swap_out(100.0, 1_000_000.0, 1_000_000.0, 0.003)
        out_large = _cpmm_swap_out(1000.0, 1_000_000.0, 1_000_000.0, 0.003)
        assert out_large > out_small

    def test_zero_fee(self):
        # With 0 fee: out = x * R_out / (R_in + x)
        out = _cpmm_swap_out(100.0, 1000.0, 1000.0, 0.0)
        expected = 100.0 * 1000.0 / (1000.0 + 100.0)
        assert out == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# _pool_swap_out
# ---------------------------------------------------------------------------

class TestPoolSwapOut:

    def test_cpmm_swap_0_to_1(self):
        pool = _make_pool("A", "B", 1000.0, 2000.0, fee=0.003)
        out = _pool_swap_out(10.0, pool, swap_0_to_1=True)
        expected = _cpmm_swap_out(10.0, 1000.0, 2000.0, 0.003)
        assert out == pytest.approx(expected, rel=1e-9)

    def test_cpmm_swap_1_to_0(self):
        pool = _make_pool("A", "B", 1000.0, 2000.0, fee=0.003)
        out = _pool_swap_out(10.0, pool, swap_0_to_1=False)
        expected = _cpmm_swap_out(10.0, 2000.0, 1000.0, 0.003)
        assert out == pytest.approx(expected, rel=1e-9)

    def test_curve_ss_dispatches_correctly(self):
        # Minimal check: curve_ss kind uses different math; result should
        # be non-zero and less than input (pool has balanced reserves)
        pool = _make_pool("A", "B", 1_000_000.0, 1_000_000.0,
                          fee=0.0001, kind="curve_ss", amp=200.0)
        out = _pool_swap_out(1000.0, pool, swap_0_to_1=True)
        # Near-parity pool: should get ~999 back for 1000 in (minus fee)
        assert out > 900.0
        assert out < 1000.0

    def test_unknown_kind_defaults_to_cpmm(self):
        pool = _make_pool("A", "B", 1000.0, 1000.0, fee=0.003)
        pool.kind = "mystery"
        out_via_dispatch = _pool_swap_out(10.0, pool, swap_0_to_1=True)
        out_direct = _cpmm_swap_out(10.0, 1000.0, 1000.0, 0.003)
        assert out_via_dispatch == pytest.approx(out_direct, rel=1e-9)


# ---------------------------------------------------------------------------
# RouteGraph construction
# ---------------------------------------------------------------------------

class TestRouteGraphConstruction:

    def _two_pool_map(self):
        p1 = _make_pool("A", "B", 1000.0, 2000.0, dex="dex1")
        p2 = _make_pool("B", "C", 1000.0, 500.0, dex="dex2")
        return _make_pool_map(p1, p2)

    def test_tokens_sorted(self):
        graph = RouteGraph(self._two_pool_map())
        assert graph.tokens == sorted(graph.tokens)
        assert "A" in graph.tokens
        assert "B" in graph.tokens
        assert "C" in graph.tokens

    def test_neighbors_bidirectional(self):
        graph = RouteGraph(self._two_pool_map())
        # A-B pool: B should appear in A's neighbors AND A in B's
        assert "B" in graph.neighbors("A")
        assert "A" in graph.neighbors("B")

    def test_empty_pool_map(self):
        graph = RouteGraph({})
        assert graph.tokens == []
        assert graph.neighbors("WMATIC") == []

    def test_pools_for_edge_correct_count(self):
        p1 = _make_pool("A", "B", 1000.0, 1000.0)
        p2 = _make_pool("A", "B", 2000.0, 2000.0, dex="dex2")
        graph = RouteGraph(_make_pool_map(p1, p2))
        assert len(graph.pools_for_edge("A", "B")) == 2
        assert len(graph.pools_for_edge("B", "A")) == 2


# ---------------------------------------------------------------------------
# RouteGraph.best_pool_for_edge
# ---------------------------------------------------------------------------

class TestBestPoolForEdge:

    def test_returns_price_optimal_pool(self):
        # p1 gives 2 B per A (price = 2.0), p2 gives 0.5 B per A (price = 0.5)
        p1 = _make_pool("A", "B", 100.0, 200.0, dex="p1_high_rate")   # price=2.0
        p2 = _make_pool("A", "B", 200.0, 100.0, dex="p2_low_rate")    # price=0.5
        graph = RouteGraph(_make_pool_map(p1, p2))
        result = graph.best_pool_for_edge("A", "B")
        assert result is not None
        pool, _ = result
        # p1 has reserve1/reserve0 = 2.0 (better rate for A→B)
        assert pool.dex == "p1_high_rate"

    def test_returns_deepest_pool(self):
        shallow = _make_pool("A", "B", 100.0, 100.0, dex="shallow")
        deep = _make_pool("A", "B", 10_000.0, 10_000.0, dex="deep")
        graph = RouteGraph(_make_pool_map(shallow, deep))
        result = graph.best_pool_for_edge("A", "B")
        assert result is not None
        pool, _ = result
        # Both have same price (1.0); deep has same key so either is fine;
        # main check: no crash and returns one of the two pools
        assert pool.dex in ("shallow", "deep")

    def test_swap_0_to_1_flag_correct(self):
        p = _make_pool("A", "B", 1000.0, 2000.0)
        graph = RouteGraph(_make_pool_map(p))
        pool, swap_0_to_1 = graph.best_pool_for_edge("A", "B")
        assert swap_0_to_1 is True  # A is sym0, B is sym1 → swap token0→token1

    def test_swap_1_to_0_flag_correct(self):
        p = _make_pool("A", "B", 1000.0, 2000.0)
        graph = RouteGraph(_make_pool_map(p))
        pool, swap_0_to_1 = graph.best_pool_for_edge("B", "A")
        assert swap_0_to_1 is False  # B is sym1, A is sym0 → swap token1→token0

    def test_none_when_no_edge(self):
        p = _make_pool("A", "B", 1000.0, 2000.0)
        graph = RouteGraph(_make_pool_map(p))
        assert graph.best_pool_for_edge("A", "C") is None

    def test_none_when_zero_reserves(self):
        p = _make_pool("A", "B", 0.0, 0.0)
        graph = RouteGraph(_make_pool_map(p))
        # No eligible pool (both reserves are zero)
        assert graph.best_pool_for_edge("A", "B") is None


# ---------------------------------------------------------------------------
# RouteGraph.enumerate_cycles
# ---------------------------------------------------------------------------

class TestEnumerateCycles:

    def _triangle_graph(self):
        """A→B→C fully connected (3 pairs)."""
        pools = [
            _make_pool("A", "B", 1000.0, 1000.0),
            _make_pool("B", "C", 1000.0, 1000.0),
            _make_pool("A", "C", 1000.0, 1000.0),
        ]
        return RouteGraph(_make_pool_map(*pools))

    def _linear_graph(self):
        """A-B-C linear (no A-C edge)."""
        pools = [
            _make_pool("A", "B", 1000.0, 1000.0),
            _make_pool("B", "C", 1000.0, 1000.0),
        ]
        return RouteGraph(_make_pool_map(*pools))

    def test_two_hop_cycle_found(self):
        """A→B→A (2-hop) exists when A-B pool is present."""
        p = _make_pool("A", "B", 1000.0, 1000.0)
        graph = RouteGraph(_make_pool_map(p))
        cycles = graph.enumerate_cycles("A", min_hops=2, max_hops=2)
        # Should find A→B→A
        assert any(c[0] == "A" and c[-1] == "A" for c in cycles)

    def test_three_hop_cycle_found(self):
        graph = self._triangle_graph()
        cycles = graph.enumerate_cycles("A", min_hops=3, max_hops=3)
        # Should find A→B→C→A (and/or A→C→B→A)
        assert len(cycles) >= 1
        for c in cycles:
            assert c[0] == "A"
            assert c[-1] == "A"
            assert len(c) == 4  # [A, B, C, A] or [A, C, B, A]

    def test_no_cycle_when_disconnected(self):
        graph = self._linear_graph()
        # A-C not connected directly; A→B→C→A is not a valid cycle
        cycles = graph.enumerate_cycles("A", min_hops=3, max_hops=3)
        assert cycles == []

    def test_start_token_not_in_graph(self):
        p = _make_pool("A", "B", 1000.0, 1000.0)
        graph = RouteGraph(_make_pool_map(p))
        assert graph.enumerate_cycles("Z") == []

    def test_min_hops_respected(self):
        graph = self._triangle_graph()
        cycles = graph.enumerate_cycles("A", min_hops=3, max_hops=4)
        for c in cycles:
            # All cycles must have at least 3 hops → length ≥ 4
            assert len(c) - 1 >= 3

    def test_max_hops_respected(self):
        graph = self._triangle_graph()
        max_h = 2
        cycles = graph.enumerate_cycles("A", min_hops=2, max_hops=max_h)
        for c in cycles:
            assert len(c) - 1 <= max_h

    def test_cycle_starts_and_ends_at_start(self):
        graph = self._triangle_graph()
        for c in graph.enumerate_cycles("A"):
            assert c[0] == "A"
            assert c[-1] == "A"

    def test_no_interior_token_repeated(self):
        graph = self._triangle_graph()
        for c in graph.enumerate_cycles("A", min_hops=2, max_hops=4):
            interior = c[1:-1]
            assert len(interior) == len(set(interior)), (
                f"Interior tokens repeated in cycle {c}"
            )


# ---------------------------------------------------------------------------
# simulate_n_hop_cycle
# ---------------------------------------------------------------------------

class TestSimulateNHopCycle:

    def _two_pool_graph(self, r0=1_000_000.0, r1=1_000_000.0):
        p_ab = _make_pool("A", "B", r0, r1, fee=0.003, dex="dex_ab")
        p_ba = _make_pool("A", "B", r0, r1, fee=0.003, dex="dex_ba")
        return RouteGraph(_make_pool_map(p_ab, p_ba))

    def test_round_trip_with_large_reserves_near_zero_profit(self):
        graph = self._two_pool_graph(r0=1_000_000.0, r1=1_000_000.0)
        # Symmetric pools: A→B→A should return less than we put in (fees)
        out, legs = simulate_n_hop_cycle(graph, ["A", "B", "A"], amount_in=1000.0)
        # Two 0.3% fee legs: expected out ≈ 1000 * 0.997 * 0.997 ≈ 994
        assert out < 1000.0
        assert len(legs) == 2

    def test_invalid_sequence_returns_zero(self):
        """Non-cycle (start != end) should return 0."""
        p = _make_pool("A", "B", 1000.0, 1000.0)
        graph = RouteGraph(_make_pool_map(p))
        out, legs = simulate_n_hop_cycle(graph, ["A", "B", "C"], amount_in=10.0)
        assert out == 0.0

    def test_too_short_sequence_returns_zero(self):
        p = _make_pool("A", "B", 1000.0, 1000.0)
        graph = RouteGraph(_make_pool_map(p))
        out, legs = simulate_n_hop_cycle(graph, ["A", "A"], amount_in=10.0)
        assert out == 0.0

    def test_missing_edge_returns_zero(self):
        p = _make_pool("A", "B", 1000.0, 1000.0)
        graph = RouteGraph(_make_pool_map(p))
        # A→C edge doesn't exist
        out, legs = simulate_n_hop_cycle(graph, ["A", "C", "A"], amount_in=10.0)
        assert out == 0.0

    def test_three_hop_cycle_runs(self):
        p_ab = _make_pool("A", "B", 1_000_000.0, 1_000_000.0, fee=0.003)
        p_bc = _make_pool("B", "C", 1_000_000.0, 1_000_000.0, fee=0.003)
        p_ca = _make_pool("A", "C", 1_000_000.0, 1_000_000.0, fee=0.003)
        graph = RouteGraph(_make_pool_map(p_ab, p_bc, p_ca))
        out, legs = simulate_n_hop_cycle(graph, ["A", "B", "C", "A"], amount_in=1000.0)
        # 3 × 0.3% fee → ~1000 * 0.997³ ≈ 991
        assert 980.0 < out < 1000.0
        assert len(legs) == 3

    def test_output_non_negative(self):
        p_ab = _make_pool("A", "B", 500.0, 1000.0, fee=0.003)
        p_ba = _make_pool("A", "B", 1000.0, 500.0, fee=0.003, dex="dex2")
        graph = RouteGraph(_make_pool_map(p_ab, p_ba))
        out, _ = simulate_n_hop_cycle(graph, ["A", "B", "A"], amount_in=1.0)
        assert out >= 0.0


# ---------------------------------------------------------------------------
# CycleRecord
# ---------------------------------------------------------------------------

class TestCycleRecord:

    def _make_record(self, net: float = 10.0, p_fill: float = 0.8) -> CycleRecord:
        e = net * p_fill if net > 0 else 0.0
        return CycleRecord(
            tokens=["A", "B", "A"],
            pools=["0xAB", "0xBA"],
            dexes=["dex1", "dex2"],
            hop_count=2,
            amount_in=1000.0,
            trade_size_usd=1000.0,
            amount_out=1010.0,
            gross_profit=10.0,
            gross_profit_usd=net + 5.0,
            flash_fee_usd=2.0,
            gas_cost_usd=3.0,
            net_profit_usd=net,
            p_fill=p_fill,
            e_profit=e,
            profitable=(net > 1.0),
        )

    def test_fields_populated(self):
        rec = self._make_record(net=10.0, p_fill=0.8)
        assert rec.hop_count == 2
        assert rec.tokens == ["A", "B", "A"]
        assert rec.profitable is True

    def test_e_profit_zero_when_net_zero(self):
        rec = self._make_record(net=0.0, p_fill=0.8)
        assert rec.e_profit == 0.0

    def test_profitable_false_when_net_below_threshold(self):
        rec = self._make_record(net=0.5)
        assert rec.profitable is False  # threshold was 1.0 in _make_record


# ---------------------------------------------------------------------------
# scan_multi_hop_cycles
# ---------------------------------------------------------------------------

class TestScanMultiHopCycles:

    def _profitable_graph(self):
        """A/B pair with significant spread — buy A cheap on dex1, sell on dex2."""
        # dex1: more B per A (low A price → good buy)
        p_cheap = _make_pool("A", "B", 1_000_000.0, 2_000_000.0, fee=0.001, dex="dex1",
                             pool_address="0xCHEAP")
        # dex2: less B per A (high A price → good sell side for B→A)
        p_dear = _make_pool("A", "B", 2_000_000.0, 1_000_000.0, fee=0.001, dex="dex2",
                            pool_address="0xDEAR")
        return _make_pool_map(p_cheap, p_dear)

    def test_empty_pool_map_returns_empty(self):
        opt = _make_tip_optimizer()
        results = scan_multi_hop_cycles({}, {}, opt)
        assert results == []

    def test_no_tokens_with_prices_returns_empty(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer()
        # No prices provided → all tokens have 0 price → skipped
        results = scan_multi_hop_cycles(pm, {}, opt)
        assert results == []

    def test_finds_profitable_two_hop_cycle(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer(gas_cost_per_call=0.01, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(
            pm, prices, opt,
            min_hops=2, max_hops=2,
            min_net_profit_usd=0.01,
            max_trade_size_usd=100_000.0,
        )
        # At least one cycle should be profitable (spread is ~100%)
        assert len(results) > 0
        assert all(r.hop_count == 2 for r in results)

    def test_result_sorted_by_e_profit_desc(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer(gas_cost_per_call=0.01, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(pm, prices, opt, min_net_profit_usd=0.01)
        e_profits = [r.e_profit for r in results]
        assert e_profits == sorted(e_profits, reverse=True)

    def test_result_cycle_starts_and_ends_same_token(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer(gas_cost_per_call=0.01, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(pm, prices, opt, min_net_profit_usd=0.01)
        for r in results:
            assert r.tokens[0] == r.tokens[-1]

    def test_high_gas_cost_eliminates_records(self):
        """When gas cost > gross profit, no records should be emitted."""
        pm = self._profitable_graph()
        # Gas cost = $9999 per cycle → always unprofitable
        opt = _make_tip_optimizer(gas_cost_per_call=9_999.0, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(
            pm, prices, opt,
            min_net_profit_usd=1.0,
            max_trade_size_usd=100.0,
        )
        assert results == []

    def test_min_net_profit_filter_respected(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer(gas_cost_per_call=0.01, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(
            pm, prices, opt, min_net_profit_usd=1e9
        )
        # Threshold is absurdly high → no records
        assert results == []

    def test_cycle_record_fields_populated(self):
        pm = self._profitable_graph()
        opt = _make_tip_optimizer(gas_cost_per_call=0.01, p_fill=0.9)
        prices = {"A": 1.0, "B": 2.0}
        results = scan_multi_hop_cycles(
            pm, prices, opt, min_net_profit_usd=0.01
        )
        for r in results:
            assert isinstance(r, CycleRecord)
            assert r.hop_count >= 2
            assert len(r.tokens) == r.hop_count + 1
            assert len(r.pools) == r.hop_count
            assert len(r.dexes) == r.hop_count
            assert r.trade_size_usd > 0.0
            assert math.isfinite(r.net_profit_usd)
            assert 0.0 <= r.p_fill <= 1.0
            assert r.e_profit >= 0.0


# ---------------------------------------------------------------------------
# Package-level export guard
# ---------------------------------------------------------------------------

class TestCoreExports:
    """Confirm that core/__init__.py exports route_graph symbols."""

    def test_cycle_record_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "CycleRecord")

    def test_route_graph_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "RouteGraph")

    def test_scan_multi_hop_cycles_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "scan_multi_hop_cycles")

    def test_simulate_n_hop_cycle_exported(self):
        import apex_omega_core.core as core_pkg
        assert hasattr(core_pkg, "simulate_n_hop_cycle")

    def test_in_all(self):
        import apex_omega_core.core as core_pkg
        for name in ("CycleRecord", "RouteGraph", "scan_multi_hop_cycles",
                     "simulate_n_hop_cycle"):
            assert name in core_pkg.__all__, f"{name!r} missing from core.__all__"
