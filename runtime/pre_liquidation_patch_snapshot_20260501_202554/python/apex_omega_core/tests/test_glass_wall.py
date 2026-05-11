"""Glass-wall transparency tests.

Verifies that every discovered opportunity exposes all intermediate states:
C1 strike plans include ``gate_trace`` with p_net / p_fill / gate_passed, and
C2 decision plans include the same plus slippage audit fields.
"""

from __future__ import annotations

import pytest


# ── Shared route fixture ───────────────────────────────────────────────────────

def _make_route(
    reserve_in_a: float = 2_000_000.0,
    reserve_out_a: float = 2_040_000.0,
    reserve_in_b: float = 2_040_000.0,
    reserve_out_b: float = 2_120_000.0,
) -> list:
    return [
        {
            "venue": "uniswap",
            "pair": "USDC → TOKEN",
            "reserve_in": reserve_in_a,
            "reserve_out": reserve_out_a,
            "fee": 0.003,
            "price_in_usd": 1.0,
            "price_out_usd": 1.02,
            "tvl_usd": 1_500_000.0,
            "volume_24h_usd": 5_000_000.0,
            "age_in_blocks": 100.0,
        },
        {
            "venue": "quickswap",
            "pair": "TOKEN → USDC",
            "reserve_in": reserve_in_b,
            "reserve_out": reserve_out_b,
            "fee": 0.0025,
            "price_in_usd": 1.02,
            "price_out_usd": 1.0,
            "tvl_usd": 1_650_000.0,
            "volume_24h_usd": 6_000_000.0,
            "age_in_blocks": 80.0,
        },
    ]


# ── C1AggressorApex — gate_trace ───────────────────────────────────────────────

class TestC1StrikePlanGateTrace:
    """C1 strike plans must carry a gate_trace with all profitability gate inputs."""

    def _make_plan(self, p_fill: float = 1.0) -> dict:
        from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
        c1 = C1AggressorApex()
        route = _make_route()
        return c1.prepare_contract_strike(
            route,
            raw_spread=0.04,
            min_input=10_000.0,
            max_input=100_000.0,
            steps=10,
            p_fill=p_fill,
        )

    def test_gate_trace_present(self) -> None:
        plan = self._make_plan()
        assert "gate_trace" in plan

    def test_gate_trace_contains_required_keys(self) -> None:
        plan = self._make_plan()
        trace = plan["gate_trace"]
        assert "p_net" in trace
        assert "p_fill" in trace
        assert "gate_passed" in trace

    def test_gate_trace_p_fill_matches_argument(self) -> None:
        plan = self._make_plan(p_fill=0.75)
        assert plan["gate_trace"]["p_fill"] == pytest.approx(0.75)

    def test_gate_trace_gate_passed_consistent_with_action(self) -> None:
        """gate_trace.gate_passed must be consistent with the action field."""
        plan = self._make_plan(p_fill=1.0)
        trace = plan["gate_trace"]
        if plan["action"] == "STRIKE":
            # STRIKE requires gate to have passed (p_net > 0 and p_fill > 0).
            assert trace["gate_passed"] is True
        else:
            # ABORT means either gate failed or mempool said unsafe.
            # gate_passed can be True or False depending on the reason for abort.
            assert isinstance(trace["gate_passed"], bool)

    def test_gate_trace_gate_passed_false_when_p_fill_zero(self) -> None:
        plan = self._make_plan(p_fill=0.0)
        assert plan["gate_trace"]["gate_passed"] is False
        assert plan["action"] == "ABORT"

    def test_gate_trace_p_net_type_is_float(self) -> None:
        plan = self._make_plan()
        assert isinstance(plan["gate_trace"]["p_net"], float)


# ── C2SurgeonApex — gate_trace ─────────────────────────────────────────────────

class TestC2DecisionPlanGateTrace:
    """C2 decision plans must carry a gate_trace with profitability and slippage audit."""

    def _make_plan(self, p_fill: float = 1.0) -> dict:
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        c2 = C2SurgeonApex()
        route = _make_route()
        return c2.decide_contract_action(
            route,
            raw_spread=0.04,
            min_input=10_000.0,
            max_input=100_000.0,
            gas_cost=1.0,
            steps=10,
            p_fill=p_fill,
        )

    def test_gate_trace_present(self) -> None:
        plan = self._make_plan()
        assert "gate_trace" in plan

    def test_gate_trace_contains_required_keys(self) -> None:
        plan = self._make_plan()
        trace = plan["gate_trace"]
        assert "p_net" in trace
        assert "p_fill" in trace
        assert "gate_passed" in trace
        assert "total_slippage" in trace
        assert "max_slippage_exceeded" in trace

    def test_gate_trace_p_fill_matches_argument(self) -> None:
        plan = self._make_plan(p_fill=0.6)
        assert plan["gate_trace"]["p_fill"] == pytest.approx(0.6)

    def test_gate_trace_gate_passed_false_when_p_fill_zero(self) -> None:
        plan = self._make_plan(p_fill=0.0)
        assert plan["gate_trace"]["gate_passed"] is False
        assert plan["decision"] == "DO_NOTHING"

    def test_gate_trace_slippage_fields_are_numeric(self) -> None:
        plan = self._make_plan()
        trace = plan["gate_trace"]
        assert isinstance(trace["total_slippage"], float)
        assert isinstance(trace["max_slippage_exceeded"], bool)

    def test_gate_trace_max_slippage_exceeded_consistent_with_decision(self) -> None:
        """When slippage exceeds limit, decision must be DO_NOTHING."""
        plan = self._make_plan(p_fill=1.0)
        trace = plan["gate_trace"]
        if trace["max_slippage_exceeded"]:
            assert plan["decision"] == "DO_NOTHING"

    def test_gate_trace_p_net_type_is_float(self) -> None:
        plan = self._make_plan()
        assert isinstance(plan["gate_trace"]["p_net"], float)
