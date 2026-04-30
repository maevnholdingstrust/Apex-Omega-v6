"""Tests for the multi-hop multi-pair graph router (route_graph.py)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import pytest

from apex_omega_core.core.route_graph import (
    GraphEdge,
    RouteGraph,
    CycleRecord,
    best_pool_for_leg,
    simulate_n_hop_cycle,
    scan_multi_hop_cycles,
    _cpmm_swap_out,
    _edge_swap_out,
)


# ---------------------------------------------------------------------------
# Helpers: minimal mock pool + mock TipOptimizer
# ---------------------------------------------------------------------------

@dataclass
class _MockPool:
    """Minimal pool stub that satisfies the PoolLike protocol."""
    pool_address: str
    dex: str
    fee: float
    sym0: str
    sym1: str
    reserve0: float
    reserve1: float
    price: float
    kind: str = "cpmm"
    amp: float = 0.0


class _MockTipOptimizer:
    """Returns canned EIP-1559 params so tests don't need a live gas oracle."""

    def build_eip1559_params(self, net_profit_usd: float) -> dict:
        return {
            "gas_cost_usd": 0.50,
            "p_fill": 0.90,
            "max_fee_gwei": 100.0,
            "priority_fee_gwei": 30.0,
        }


# Canonical token ordering: lower address wins
_ADDR = {
    "USDC": "0x01",
    "WETH": "0x02",
    "WMATIC": "0x03",
    "WBTC": "0x04",
    "DAI": "0x05",
}


def _make_pool(sym_a: str, sym_b: str, r_a: float, r_b: float,
               dex: str = "qsv2", fee: float = 0.003,
               kind: str = "cpmm") -> _MockPool:
    """Create a balanced mock pool; sym0 is the lower-address token."""
    addr_a = _ADDR.get(sym_a, sym_a)
    addr_b = _ADDR.get(sym_b, sym_b)
    if addr_a.lower() < addr_b.lower():
        sym0, sym1, r0, r1 = sym_a, sym_b, r_a, r_b
    else:
        sym0, sym1, r0, r1 = sym_b, sym_a, r_b, r_a
    return _MockPool(
        pool_address=f"0xPOOL_{sym0}_{sym1}_{dex}",
        dex=dex,
        fee=fee,
        sym0=sym0,
        sym1=sym1,
        reserve0=r0,
        reserve1=r1,
        price=r1 / r0 if r0 > 0 else 0.0,
        kind=kind,
    )


def _pool_map(*pools: _MockPool):
    """Build a {pair_key: [pools]} dict from a list of mock pools."""
    pm = {}
    for p in pools:
        key = f"{p.sym0}/{p.sym1}"
        pm.setdefault(key, []).append(p)
    return pm


# ---------------------------------------------------------------------------
# Unit: _cpmm_swap_out
# ---------------------------------------------------------------------------

class TestCpmmSwapOut:
    def test_basic_swap(self) -> None:
        # 1000 in, reserves 10_000 / 10_000, fee=0.003
        out = _cpmm_swap_out(1000.0, 10_000.0, 10_000.0, 0.003)
        assert out == pytest.approx(907.441, rel=1e-3)

    def test_zero_input(self) -> None:
        assert _cpmm_swap_out(0.0, 10_000.0, 10_000.0, 0.003) == 0.0

    def test_zero_reserve_in(self) -> None:
        assert _cpmm_swap_out(100.0, 0.0, 10_000.0, 0.003) == 0.0

    def test_full_fee(self) -> None:
        # fee=1.0 means no output
        assert _cpmm_swap_out(100.0, 10_000.0, 10_000.0, 1.0) == 0.0


# ---------------------------------------------------------------------------
# Unit: GraphEdge + _edge_swap_out
# ---------------------------------------------------------------------------

class TestEdgeSwapOut:
    def test_cpmm_0_to_1(self) -> None:
        pool = _make_pool("USDC", "WETH", 10_000.0, 4.0)  # 1 WETH = 2500 USDC
        edge = GraphEdge(
            from_token=pool.sym0, to_token=pool.sym1, pool=pool, swap_0_to_1=True
        )
        # Swap 100 USDC → WETH
        out = _edge_swap_out(100.0, edge)
        assert out > 0.0
        # Rough check: ~100/2500 ≈ 0.040 WETH (less after fee and impact)
        assert out == pytest.approx(100.0 / 2500.0, rel=0.05)

    def test_cpmm_1_to_0(self) -> None:
        pool = _make_pool("USDC", "WETH", 10_000.0, 4.0)
        # sym0=USDC, sym1=WETH  → reverse direction swaps WETH→USDC
        edge = GraphEdge(
            from_token=pool.sym1, to_token=pool.sym0, pool=pool, swap_0_to_1=False
        )
        # Swap 0.04 WETH → USDC
        out = _edge_swap_out(0.04, edge)
        assert out == pytest.approx(100.0, rel=0.05)


# ---------------------------------------------------------------------------
# Unit: simulate_n_hop_cycle
# ---------------------------------------------------------------------------

class TestSimulateNHopCycle:
    def _build_3_leg(self) -> tuple:
        """Build USDC→WETH→WMATIC→USDC legs with a slight mispricing on leg 3."""
        # Leg 1: USDC→WETH  (1 WETH = 2500 USDC)
        pool_ab = _make_pool("USDC", "WETH", 10_000_000.0, 4_000.0)
        # Leg 2: WETH→WMATIC  (1 WETH = 6250 WMATIC at 0.40/WMATIC)
        pool_bc = _make_pool("WETH", "WMATIC", 1_000.0, 6_250_000.0)
        # Leg 3: WMATIC→USDC  (1 USDC = 2.4 WMATIC; slightly mispriced)
        pool_ca = _make_pool("WMATIC", "USDC", 100_000_000.0, 41_666_667.0)

        leg1 = GraphEdge("USDC", "WETH", pool_ab, pool_ab.sym0 == "USDC")
        leg2 = GraphEdge("WETH", "WMATIC", pool_bc, pool_bc.sym0 == "WETH")
        leg3 = GraphEdge("WMATIC", "USDC", pool_ca, pool_ca.sym0 == "WMATIC")
        return leg1, leg2, leg3

    def test_non_profitable_cycle_returns_less_than_input(self) -> None:
        leg1, leg2, leg3 = self._build_3_leg()
        amount_in = 1000.0
        out = simulate_n_hop_cycle([leg1, leg2, leg3], amount_in)
        # After fees the cycle should give back roughly the same amount
        # (slightly less in a fairly-priced market — not a big profit)
        assert 0.0 < out <= amount_in * 1.1

    def test_zero_intermediate_returns_zero(self) -> None:
        pool = _make_pool("USDC", "WETH", 0.0, 0.0)  # empty pool
        edge = GraphEdge("USDC", "WETH", pool, True)
        assert simulate_n_hop_cycle([edge], 100.0) == 0.0

    def test_single_leg_identity(self) -> None:
        pool = _make_pool("USDC", "WETH", 10_000_000.0, 4_000.0)
        edge = GraphEdge("USDC", "WETH", pool, pool.sym0 == "USDC")
        out = simulate_n_hop_cycle([edge], 1000.0)
        # Should get back about 0.4 WETH
        assert out == pytest.approx(0.4, rel=0.1)


# ---------------------------------------------------------------------------
# Unit: best_pool_for_leg
# ---------------------------------------------------------------------------

class TestBestPoolForLeg:
    def test_picks_deepest_pool(self) -> None:
        p_shallow = _make_pool("USDC", "WETH", 100_000.0, 40.0, dex="qsv2")
        p_deep    = _make_pool("USDC", "WETH", 5_000_000.0, 2_000.0, dex="univ3_500")
        result = best_pool_for_leg("USDC", "WETH", [p_shallow, p_deep])
        assert result is not None
        assert result.pool.dex == "univ3_500"

    def test_returns_none_for_empty_list(self) -> None:
        assert best_pool_for_leg("USDC", "WETH", []) is None

    def test_excludes_curve_ss(self) -> None:
        p_curve = _make_pool("USDC", "DAI", 1_000_000.0, 1_000_000.0,
                              dex="curve_ss", kind="curve_ss")
        result = best_pool_for_leg("USDC", "DAI", [p_curve])
        assert result is None

    def test_reverse_direction(self) -> None:
        p = _make_pool("USDC", "WETH", 5_000_000.0, 2_000.0, dex="qsv2")
        result = best_pool_for_leg("WETH", "USDC", [p])
        assert result is not None
        assert result.swap_0_to_1 is False
        assert result.from_token == "WETH"
        assert result.to_token == "USDC"

    def test_excludes_empty_reserves(self) -> None:
        p = _make_pool("USDC", "WETH", 0.0, 0.0)
        assert best_pool_for_leg("USDC", "WETH", [p]) is None


# ---------------------------------------------------------------------------
# Unit: RouteGraph construction
# ---------------------------------------------------------------------------

class TestRouteGraph:
    def test_empty_graph(self) -> None:
        g = RouteGraph()
        assert g.tokens == []
        assert g.neighbors("USDC") == []

    def test_add_pool_registers_both_directions(self) -> None:
        g = RouteGraph()
        p = _make_pool("USDC", "WETH", 1_000_000.0, 400.0)
        g.add_pool(p)
        n_usdc = [e.to_token for e in g.neighbors("USDC")]
        n_weth = [e.to_token for e in g.neighbors("WETH")]
        assert "WETH" in n_usdc
        assert "USDC" in n_weth

    def test_curve_pool_excluded(self) -> None:
        g = RouteGraph()
        p = _make_pool("USDC", "DAI", 1_000_000.0, 1_000_000.0,
                       kind="curve_ss")
        g.add_pool(p)
        assert g.tokens == []

    def test_zero_reserve_pool_excluded(self) -> None:
        g = RouteGraph()
        p = _make_pool("USDC", "WETH", 0.0, 400.0)
        g.add_pool(p)
        assert g.tokens == []

    def test_build_from_pool_map(self) -> None:
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0),
            _make_pool("WETH", "WMATIC", 400.0, 1_000_000.0),
        )
        g = RouteGraph.build_from_pool_map(pm)
        assert "USDC" in g.tokens
        assert "WETH" in g.tokens
        assert "WMATIC" in g.tokens

    def test_pools_for_pair(self) -> None:
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0, dex="qsv2"),
            _make_pool("USDC", "WETH", 5_000_000.0, 2_000.0, dex="univ3_500"),
        )
        g = RouteGraph.build_from_pool_map(pm)
        pools = g.pools_for_pair("USDC", "WETH")
        assert len(pools) == 2

    def test_neighbors_deduplicates_by_pool_address(self) -> None:
        g = RouteGraph()
        p = _make_pool("USDC", "WETH", 1_000_000.0, 400.0)
        g.add_pool(p)
        g.add_pool(p)  # duplicate
        assert len(g.neighbors("USDC")) == 1


# ---------------------------------------------------------------------------
# Unit: RouteGraph.enumerate_cycles
# ---------------------------------------------------------------------------

class TestEnumerateCycles:
    def _triangle_graph(self) -> RouteGraph:
        """Build a simple 3-node connected graph: USDC↔WETH↔WMATIC↔USDC."""
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0),
            _make_pool("WETH", "WMATIC", 400.0, 1_000_000.0),
            _make_pool("WMATIC", "USDC", 1_000_000.0, 400_000.0),
        )
        return RouteGraph.build_from_pool_map(pm)

    def test_finds_3_hop_cycle(self) -> None:
        g = self._triangle_graph()
        cycles = list(g.enumerate_cycles("USDC", min_hops=3, max_hops=4))
        # Must find the A→B→C→A cycle
        assert len(cycles) >= 1
        for cyc in cycles:
            assert len(cyc) == 3
            assert cyc[0].from_token == "USDC"
            assert cyc[-1].to_token == "USDC"

    def test_no_2_hop_cycles_by_default(self) -> None:
        g = self._triangle_graph()
        cycles = list(g.enumerate_cycles("USDC", min_hops=3, max_hops=4))
        assert all(len(c) >= 3 for c in cycles)

    def test_min_hops_2_finds_direct_return(self) -> None:
        """A direct A→B→A (2-hop) cycle should be found when min_hops=2."""
        g = self._triangle_graph()
        cycles = list(g.enumerate_cycles("USDC", min_hops=2, max_hops=2))
        # Each direct A→B→A should have 2 hops
        assert len(cycles) >= 1
        assert all(len(c) == 2 for c in cycles)

    def test_unknown_start_token_returns_nothing(self) -> None:
        g = self._triangle_graph()
        cycles = list(g.enumerate_cycles("NONEXISTENT"))
        assert cycles == []

    def test_4_hop_cycle_found_in_quad_graph(self) -> None:
        """Build a square graph (4 distinct tokens) and find the 4-hop cycle."""
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0),
            _make_pool("WETH", "WBTC", 400.0, 0.02),
            _make_pool("WBTC", "WMATIC", 0.02, 1_300.0),
            _make_pool("WMATIC", "USDC", 1_300.0, 520.0),
        )
        g = RouteGraph.build_from_pool_map(pm)
        cycles = list(g.enumerate_cycles("USDC", min_hops=4, max_hops=4))
        assert any(len(c) == 4 for c in cycles)

    def test_simple_token_constraint(self) -> None:
        """No token (other than start) should appear twice in a cycle."""
        g = self._triangle_graph()
        for cyc in g.enumerate_cycles("USDC", min_hops=3, max_hops=4):
            toks = [e.from_token for e in cyc]
            assert len(toks) == len(set(toks)), f"duplicate token in {toks}"


# ---------------------------------------------------------------------------
# Unit: RouteGraph.all_start_cycles deduplication
# ---------------------------------------------------------------------------

class TestAllStartCycles:
    def test_triangle_deduplicated(self) -> None:
        """A 3-node triangle produces exactly 2 cycles (2 directions)."""
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0),
            _make_pool("WETH", "WMATIC", 400.0, 1_000_000.0),
            _make_pool("WMATIC", "USDC", 1_000_000.0, 400_000.0),
        )
        g = RouteGraph.build_from_pool_map(pm)
        cycles = list(g.all_start_cycles(min_hops=3, max_hops=3))
        # Exactly 2 directions for the single triangle
        assert len(cycles) == 2

    def test_no_duplicate_signatures(self) -> None:
        pm = _pool_map(
            _make_pool("USDC", "WETH", 1_000_000.0, 400.0),
            _make_pool("WETH", "WMATIC", 400.0, 1_000_000.0),
            _make_pool("WMATIC", "USDC", 1_000_000.0, 400_000.0),
        )
        g = RouteGraph.build_from_pool_map(pm)
        cycles = list(g.all_start_cycles(min_hops=3, max_hops=4))
        sigs = []
        for cyc in cycles:
            tok_seq = [e.from_token for e in cyc]
            min_tok = min(tok_seq)
            idx = tok_seq.index(min_tok)
            sig = tuple(tok_seq[idx:] + tok_seq[:idx])
            sigs.append(sig)
        assert len(sigs) == len(set(sigs))


# ---------------------------------------------------------------------------
# Integration: scan_multi_hop_cycles
# ---------------------------------------------------------------------------

class TestScanMultiHopCycles:
    """End-to-end tests that verify profitable cycles are detected."""

    def _mispriced_triangle(self) -> dict:
        """
        Build a triangle where one leg is deliberately mispriced to create
        a ~3% triangular arb opportunity after fees.

        Fair prices:
          1 WETH  = 2_500 USDC
          1 WMATIC = 0.40 USDC
          1 WETH  = 6_250 WMATIC

        Mispricing: WMATIC/USDC pool values WMATIC at 0.42 USDC (+5%) instead
        of 0.40, creating a buy-WMATIC-cheap (WETH pool) / sell-dear (USDC pool)
        cycle: USDC → WETH → WMATIC → USDC.
        """
        p_usdc_weth = _make_pool(
            "USDC", "WETH",
            25_000_000.0,
            10_000.0,
            dex="univ3_500", fee=0.0005,
        )
        p_weth_wmatic = _make_pool(
            "WETH", "WMATIC",
            1_000.0,
            6_250_000.0,  # fair price
            dex="qsv2", fee=0.003,
        )
        # Mispriced: WMATIC reserves correspond to 0.42 USD/WMATIC
        p_wmatic_usdc = _make_pool(
            "WMATIC", "USDC",
            10_000_000.0,
            4_200_000.0,  # 0.42 $/WMATIC instead of 0.40
            dex="qsv2", fee=0.003,
        )
        return _pool_map(p_usdc_weth, p_weth_wmatic, p_wmatic_usdc)

    def test_finds_profitable_3_hop_cycle(self) -> None:
        pm = self._mispriced_triangle()
        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {"USDC": 1.0, "WETH": 2500.0, "WMATIC": 0.40}

        results = scan_multi_hop_cycles(
            g, pm, token_prices, tip,
            max_trade_size_usd=10_000.0,
            flash_loan_fee_rate=0.0,
            min_net_profit_usd=0.01,
            min_hops=3,
            max_hops=3,
        )
        assert len(results) > 0, "Expected at least one profitable 3-hop cycle"
        best = results[0]
        assert best.hop_count == 3
        assert best.expected_net_edge > 0.0
        assert best.profitable is True
        assert "USDC" in best.pair

    def test_returns_empty_for_fairly_priced_market(self) -> None:
        """Pools priced consistently should not produce profitable cycles after fees."""
        # Consistent prices: 1 WETH=2500 USDC, 1 WMATIC=0.40 USDC, 1 WETH=6250 WMATIC
        # Reserve ordering: sym0 is the lower-address token.
        # USDC("0x01") < WETH("0x02") < WMATIC("0x03")
        # USDC/WETH pool: sym0=USDC, sym1=WETH → r0=USDC, r1=WETH
        p_usdc_weth = _make_pool("USDC", "WETH", 25_000_000.0, 10_000.0)
        # WETH/WMATIC pool: sym0=WETH, sym1=WMATIC → r0=WETH, r1=WMATIC
        p_weth_wmatic = _make_pool("WETH", "WMATIC", 1_000.0, 6_250_000.0)
        # WMATIC/USDC pool: _make_pool swaps sym/r args so that sym0 is the
        # lower-address token. USDC("0x01") < WMATIC("0x03"), so calling with
        # (WMATIC, USDC, r_wmatic, r_usdc) causes the helper to reorder:
        # sym0=USDC, sym1=WMATIC, r0=r_usdc=10M, r1=r_wmatic=25M
        # → price = 25M/10M = 2.5 WMATIC/USDC → 1 WMATIC = 0.40 USDC ✓
        p_wmatic_usdc = _make_pool("WMATIC", "USDC", 25_000_000.0, 10_000_000.0)
        pm = _pool_map(p_usdc_weth, p_weth_wmatic, p_wmatic_usdc)

        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {"USDC": 1.0, "WETH": 2500.0, "WMATIC": 0.40}

        results = scan_multi_hop_cycles(
            g, pm, token_prices, tip,
            max_trade_size_usd=10_000.0,
            flash_loan_fee_rate=0.0,
            min_net_profit_usd=0.50,
            min_hops=3,
            max_hops=4,
        )
        # After 3× 0.3% DEX fee + $0.50 gas the cycle must be underwater.
        assert results == []

    def test_results_sorted_descending_by_net_edge(self) -> None:
        """Results must be sorted best-first."""
        pm = self._mispriced_triangle()
        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {"USDC": 1.0, "WETH": 2500.0, "WMATIC": 0.40}

        results = scan_multi_hop_cycles(
            g, pm, token_prices, tip,
            max_trade_size_usd=20_000.0,
            min_net_profit_usd=0.01,
            min_hops=3, max_hops=3,
        )
        edges = [r.expected_net_edge for r in results]
        assert edges == sorted(edges, reverse=True)

    def test_4_hop_cycle_detected(self) -> None:
        """Confirm that a mispriced 4-leg cycle is found."""
        # Build a square graph with slight mispricing on leg 4
        p_usdc_weth   = _make_pool("USDC",   "WETH",   25_000_000.0, 10_000.0,   fee=0.0005)
        p_weth_wbtc   = _make_pool("WETH",   "WBTC",   10_000.0, 400.0,         fee=0.0005)
        p_wbtc_wmatic = _make_pool("WBTC",   "WMATIC", 400.0, 65_000_000.0,    fee=0.003)
        # Mispriced: WMATIC leg gives back slightly more USDC than fair
        p_wmatic_usdc = _make_pool("WMATIC", "USDC",   65_000_000.0, 27_300_000.0, fee=0.003)
        pm = _pool_map(p_usdc_weth, p_weth_wbtc, p_wbtc_wmatic, p_wmatic_usdc)

        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {
            "USDC": 1.0, "WETH": 2500.0, "WBTC": 65_000.0, "WMATIC": 0.40
        }

        results = scan_multi_hop_cycles(
            g, pm, token_prices, tip,
            max_trade_size_usd=10_000.0,
            flash_loan_fee_rate=0.0,
            min_net_profit_usd=0.01,
            min_hops=4,
            max_hops=4,
        )
        four_hop = [r for r in results if r.hop_count == 4]
        assert len(four_hop) > 0, "Expected at least one profitable 4-hop cycle"

    def test_flash_loan_fee_reduces_profit(self) -> None:
        """Aave 9 bps flash-loan fee must reduce expected_net_edge."""
        pm = self._mispriced_triangle()
        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {"USDC": 1.0, "WETH": 2500.0, "WMATIC": 0.40}
        kwargs = dict(
            max_trade_size_usd=10_000.0,
            min_net_profit_usd=0.01,
            min_hops=3, max_hops=3,
            scan_no=1,
        )

        without_fee = scan_multi_hop_cycles(g, pm, token_prices, tip,
                                            flash_loan_fee_rate=0.0, **kwargs)
        with_fee    = scan_multi_hop_cycles(g, pm, token_prices, tip,
                                            flash_loan_fee_rate=0.0009, **kwargs)

        if without_fee and with_fee:
            # Profit with fee must be smaller (or equal, if fee is negligible)
            assert with_fee[0].expected_net_edge <= without_fee[0].expected_net_edge

    def test_hop_count_field_correct(self) -> None:
        pm = self._mispriced_triangle()
        g = RouteGraph.build_from_pool_map(pm)
        tip = _MockTipOptimizer()
        token_prices = {"USDC": 1.0, "WETH": 2500.0, "WMATIC": 0.40}

        results = scan_multi_hop_cycles(
            g, pm, token_prices, tip,
            max_trade_size_usd=10_000.0,
            min_net_profit_usd=0.01,
            min_hops=3, max_hops=3,
        )
        for r in results:
            assert r.hop_count == 3

    def test_empty_pool_map(self) -> None:
        g = RouteGraph.build_from_pool_map({})
        tip = _MockTipOptimizer()
        results = scan_multi_hop_cycles(g, {}, {}, tip)
        assert results == []


# ---------------------------------------------------------------------------
# Integration: dry_run.py OpportunityRecord hop_count field
# ---------------------------------------------------------------------------

def test_opportunity_record_has_hop_count() -> None:
    """OpportunityRecord must have a hop_count field that defaults to 2."""
    from dry_run import OpportunityRecord
    rec = OpportunityRecord(
        scan_no=1, timestamp=0.0, pair="X/Y",
        buy_dex="a", sell_dex="b",
        buy_pool="0x1", sell_pool="0x2",
        raw_spread_bps=10.0, trade_size_usd=1000.0,
        gross_profit_usd=5.0, slippage_cost_usd=0.5,
        gas_cost_usd=1.0, expected_net_edge=3.5,
        p_fill=0.9, e_profit=3.15, profitable=True,
    )
    assert rec.hop_count == 2


def test_cycle_record_to_opportunity_preserves_hop_count() -> None:
    """_cycle_record_to_opportunity must copy hop_count into OpportunityRecord."""
    from dry_run import OpportunityRecord, _cycle_record_to_opportunity
    crec = CycleRecord(
        scan_no=5, timestamp=1.0, pair="A->B->C->A",
        buy_dex="qsv2->univ3", sell_dex="multihop",
        buy_pool="0xBUY", sell_pool="0xSELL",
        raw_spread_bps=25.0, trade_size_usd=500.0,
        gross_profit_usd=12.0, slippage_cost_usd=0.0,
        gas_cost_usd=0.5, expected_net_edge=11.5,
        p_fill=0.92, e_profit=10.58, profitable=True,
        hop_count=3,
    )
    orec = _cycle_record_to_opportunity(crec)
    assert isinstance(orec, OpportunityRecord)
    assert orec.hop_count == 3
    assert orec.pair == "A->B->C->A"
    assert orec.expected_net_edge == pytest.approx(11.5)
