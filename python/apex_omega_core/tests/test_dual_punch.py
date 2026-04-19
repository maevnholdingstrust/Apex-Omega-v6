"""Tests for the Dual Punch strategy (dual_punch.py)."""

import copy
import pytest

from apex_omega_core.strategies.dual_punch import (
    DualPunchEngine,
    DualPunchParams,
    DualPunchCycleResult,
    PunchResult,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_route(
    reserve_in_a: float = 2_000_000.0,
    reserve_out_a: float = 2_040_000.0,
    reserve_in_b: float = 2_040_000.0,
    reserve_out_b: float = 2_120_000.0,
) -> list:
    """Build a 2-hop test route with deep liquidity so all health gates pass."""
    return [
        {
            'venue': 'uniswap',
            'pair': 'USDC → TOKEN',
            'reserve_in': reserve_in_a,
            'reserve_out': reserve_out_a,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.02,
            'tvl_usd': 1_500_000.0,
            'volume_24h_usd': 5_000_000.0,
            'age_in_blocks': 100.0,
        },
        {
            'venue': 'quickswap',
            'pair': 'TOKEN → USDC',
            'reserve_in': reserve_in_b,
            'reserve_out': reserve_out_b,
            'fee': 0.0025,
            'price_in_usd': 1.02,
            'price_out_usd': 1.0,
            'tvl_usd': 1_650_000.0,
            'volume_24h_usd': 6_000_000.0,
            'age_in_blocks': 80.0,
        },
    ]


def _default_params(**overrides) -> DualPunchParams:
    base = DualPunchParams(
        p1_success=0.95,
        failure_loss1=0.0,
        gas_cost1=5.0,
        flash_fee_rate1=0.0009,
        safety_beta1=0.005,
        min_profit1=-1e9,   # disable threshold so EV alone governs
        p1_min=0.0,
        p2_success=0.95,
        failure_loss2=0.0,
        gas_cost2=5.0,
        flash_fee_rate2=0.0009,
        safety_beta2=0.005,
        min_profit2=-1e9,
        p2_min=0.0,
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Flash fee and safe output helpers
# ---------------------------------------------------------------------------

class TestFlashFeeAndSafeOutput:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_flash_fee_scales_linearly(self):
        assert self.engine._flash_fee(100_000.0, 0.0009) == pytest.approx(90.0)

    def test_flash_fee_zero_rate(self):
        assert self.engine._flash_fee(100_000.0, 0.0) == 0.0

    def test_safe_output_multiplicative_buffer(self):
        raw = 1_000.0
        result = self.engine._safe_output(raw, beta=0.01)
        assert result == pytest.approx(990.0)

    def test_safe_output_zero_beta(self):
        assert self.engine._safe_output(500.0, beta=0.0) == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# EV formula
# ---------------------------------------------------------------------------

class TestEVFormula:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_ev_positive_case(self):
        # p=1, no failure loss, positive net profit → EV = net_profit
        ev = self.engine._compute_ev(net_profit=100.0, p_success=1.0, failure_loss=0.0)
        assert ev == pytest.approx(100.0)

    def test_ev_negative_when_net_loss_and_high_failure(self):
        # p=0.5, net_profit=-50, L=100 → EV = 0.5*(-50) - 0.5*100 = -25 - 50 = -75
        ev = self.engine._compute_ev(net_profit=-50.0, p_success=0.5, failure_loss=100.0)
        assert ev == pytest.approx(-75.0)

    def test_ev_probability_clamped(self):
        # p > 1 should be clamped to 1.0
        ev = self.engine._compute_ev(net_profit=100.0, p_success=1.5, failure_loss=0.0)
        assert ev == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Strike decision
# ---------------------------------------------------------------------------

class TestStrikeDecision:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_strike_when_all_conditions_met(self):
        ok, reason = self.engine._strike_decision(
            ev=10.0, net_profit=5.0, p_success=0.95, min_profit=0.0, p_min=0.5
        )
        assert ok is True

    def test_no_strike_when_ev_zero(self):
        ok, _ = self.engine._strike_decision(
            ev=0.0, net_profit=5.0, p_success=0.95, min_profit=0.0, p_min=0.0
        )
        assert ok is False

    def test_no_strike_when_ev_negative(self):
        ok, _ = self.engine._strike_decision(
            ev=-1.0, net_profit=5.0, p_success=0.95, min_profit=0.0, p_min=0.0
        )
        assert ok is False

    def test_no_strike_when_p_below_p_min(self):
        ok, reason = self.engine._strike_decision(
            ev=10.0, net_profit=5.0, p_success=0.3, min_profit=0.0, p_min=0.5
        )
        assert ok is False
        assert "p_success" in reason

    def test_no_strike_when_net_profit_below_min(self):
        ok, reason = self.engine._strike_decision(
            ev=10.0, net_profit=1.0, p_success=0.95, min_profit=5.0, p_min=0.0
        )
        assert ok is False
        assert "net_profit" in reason


# ---------------------------------------------------------------------------
# Module B — state mutation
# ---------------------------------------------------------------------------

class TestStateMutation:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_mutate_reduces_reserve_out(self):
        route = _make_route()
        original_r_out = route[0]['reserve_out']

        s1 = self.engine.mutate_state(route, x1=10_000.0)

        # reserve_in grows (more tokens deposited)
        assert s1[0]['reserve_in'] > route[0]['reserve_in']
        # reserve_out shrinks (tokens removed)
        assert s1[0]['reserve_out'] < original_r_out

    def test_mutate_does_not_modify_original(self):
        route = _make_route()
        original = copy.deepcopy(route)
        self.engine.mutate_state(route, x1=50_000.0)
        assert route[0]['reserve_in'] == original[0]['reserve_in']
        assert route[0]['reserve_out'] == original[0]['reserve_out']

    def test_mutate_zero_input_leaves_reserves_unchanged(self):
        route = _make_route()
        s1 = self.engine.mutate_state(route, x1=0.0)
        # With x1=0, the AMM swap output is 0 so reserves should be unchanged.
        assert s1[0]['reserve_in'] == pytest.approx(route[0]['reserve_in'])
        assert s1[0]['reserve_out'] == pytest.approx(route[0]['reserve_out'])

    def test_mutate_chain_propagates_through_hops(self):
        """The output of hop-1 becomes the input of hop-2."""
        route = _make_route()
        x1 = 5_000.0
        s1 = self.engine.mutate_state(route, x1=x1)

        # Both legs must have changed reserves.
        assert s1[0]['reserve_in'] != route[0]['reserve_in']
        assert s1[1]['reserve_in'] != route[1]['reserve_in']


# ---------------------------------------------------------------------------
# Module A — Punch 1 evaluation
# ---------------------------------------------------------------------------

class TestPunch1Evaluation:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_punch1_returns_punch_result(self):
        route = _make_route()
        params = _default_params()
        result = self.engine.evaluate_punch1(
            route=route, params=params, min_input=1_000.0, max_input=50_000.0, steps=10
        )
        assert isinstance(result, PunchResult)
        assert result.optimal_input > 0.0

    def test_punch1_route_type_is_same(self):
        route = _make_route()
        result = self.engine.evaluate_punch1(
            route=route, params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=10
        )
        assert result.route_type == 'same'

    def test_punch1_no_strike_when_p_below_p_min(self):
        route = _make_route()
        params = _default_params(p1_success=0.3, p1_min=0.9)
        result = self.engine.evaluate_punch1(
            route=route, params=params,
            min_input=1_000.0, max_input=50_000.0, steps=5
        )
        assert result.should_strike is False

    def test_punch1_ev_formula_components_consistent(self):
        """Verify that EV = p*Π_net - (1-p)*L given the reported values."""
        route = _make_route()
        params = _default_params(p1_success=0.8, failure_loss1=50.0)
        result = self.engine.evaluate_punch1(
            route=route, params=params,
            min_input=1_000.0, max_input=20_000.0, steps=8
        )
        expected_ev = 0.8 * result.net_profit - 0.2 * 50.0
        assert result.ev == pytest.approx(expected_ev, abs=1e-9)


# ---------------------------------------------------------------------------
# Module C — Punch 2 evaluation
# ---------------------------------------------------------------------------

class TestPunch2Evaluation:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_punch2_evaluates_on_mutated_state(self):
        route = _make_route()
        params = _default_params()
        s1 = self.engine.mutate_state(route, x1=10_000.0)

        result = self.engine.evaluate_punch2(
            s1_route=s1, params=params,
            min_input=1_000.0, max_input=50_000.0, steps=8
        )
        assert isinstance(result, PunchResult)

    def test_punch2_route_type_is_same_or_reverse_or_none(self):
        route = _make_route()
        s1 = self.engine.mutate_state(route, x1=5_000.0)
        result = self.engine.evaluate_punch2(
            s1_route=s1, params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=8
        )
        assert result.route_type in ('same', 'reverse', 'none')

    def test_punch2_returns_none_route_when_no_positive_ev(self):
        """When EV is negative for all variants, route_type == 'none'."""
        # Create a route where no profit is possible: reserve_out < reserve_in
        route = _make_route(reserve_in_a=5_000_000.0, reserve_out_a=1_000.0,
                            reserve_in_b=5_000_000.0, reserve_out_b=1_000.0)
        s1 = self.engine.mutate_state(route, x1=1_000.0)
        params = _default_params(min_profit2=1e10)  # impossibly high threshold
        result = self.engine.evaluate_punch2(
            s1_route=s1, params=params,
            min_input=1_000.0, max_input=5_000.0, steps=4
        )
        assert result.should_strike is False
        assert result.route_type == 'none'

    def test_punch2_accepts_alternate_routes(self):
        route = _make_route()
        s1 = self.engine.mutate_state(route, x1=10_000.0)
        alt = _make_route(
            reserve_in_a=3_000_000.0, reserve_out_a=3_060_000.0,
            reserve_in_b=3_060_000.0, reserve_out_b=3_180_000.0,
        )
        result = self.engine.evaluate_punch2(
            s1_route=s1, params=_default_params(),
            alternate_routes=[alt],
            min_input=1_000.0, max_input=50_000.0, steps=8
        )
        assert result.route_type in ('same', 'reverse', 'alternate_0', 'none')


# ---------------------------------------------------------------------------
# Full cycle
# ---------------------------------------------------------------------------

class TestDualPunchCycle:
    def setup_method(self):
        self.engine = DualPunchEngine()

    def test_cycle_returns_result_object(self):
        route = _make_route()
        result = self.engine.run_dual_punch_cycle(
            route=route,
            params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=10,
        )
        assert isinstance(result, DualPunchCycleResult)
        assert isinstance(result.punch1, PunchResult)

    def test_cycle_rejects_when_punch1_ev_negative(self):
        """If Punch 1 has no positive EV, cycle is rejected and Punch 2 is None."""
        route = _make_route()
        # Force rejection by demanding impossibly high min profit.
        params = _default_params(min_profit1=1e12)
        result = self.engine.run_dual_punch_cycle(
            route=route, params=params,
            min_input=1_000.0, max_input=50_000.0, steps=5,
        )
        assert result.punch1.should_strike is False
        assert result.punch2 is None

    def test_cycle_s1_route_differs_from_s0(self):
        route = _make_route()
        result = self.engine.run_dual_punch_cycle(
            route=route, params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=10,
        )
        if result.punch1.should_strike:
            # s1 must reflect the post-execution reserve changes.
            assert result.s1_route[0]['reserve_in'] != route[0]['reserve_in']

    def test_cycle_ev_formula(self):
        """EV_cycle = EV1 + I2 * EV2."""
        route = _make_route()
        result = self.engine.run_dual_punch_cycle(
            route=route, params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=10,
        )
        i2 = 1 if (result.punch2 is not None and result.punch2.should_strike) else 0
        ev2 = result.punch2.ev if result.punch2 is not None else 0.0
        expected = result.punch1.ev + i2 * ev2
        assert result.ev_cycle == pytest.approx(expected, abs=1e-9)

    def test_cycle_log_is_non_empty(self):
        route = _make_route()
        result = self.engine.run_dual_punch_cycle(
            route=route, params=_default_params(),
            min_input=1_000.0, max_input=50_000.0, steps=5,
        )
        assert len(result.cycle_log) > 0

    def test_cycle_punch2_not_run_on_s0(self):
        """Punch 2 must operate on s1, not s0.

        If s1 == s0 the strategy has not been applied correctly.  After a
        non-zero Punch-1 execution the first-hop reserve_in of s1 must be
        strictly larger than that of s0.
        """
        route = _make_route()
        result = self.engine.run_dual_punch_cycle(
            route=route, params=_default_params(),
            min_input=5_000.0, max_input=50_000.0, steps=8,
        )
        if result.punch1.should_strike and result.punch2 is not None:
            # s1 is the route Punch 2 was evaluated on.
            s1_r_in = result.s1_route[0]['reserve_in']
            s0_r_in = route[0]['reserve_in']
            assert s1_r_in > s0_r_in, (
                "Punch 2 must use the post-mutation state s1, not s0"
            )


# ---------------------------------------------------------------------------
# ExecutionRouter integration
# ---------------------------------------------------------------------------

class TestExecutionRouterDualPunch:
    def test_router_exposes_run_dual_punch_cycle(self):
        from apex_omega_core.strategies.execution_router import ExecutionRouter
        router = ExecutionRouter()
        assert hasattr(router, 'run_dual_punch_cycle')

    def test_router_dual_punch_returns_cycle_result(self):
        from apex_omega_core.strategies.execution_router import ExecutionRouter
        router = ExecutionRouter()
        route = _make_route()
        result = router.run_dual_punch_cycle(
            route=route,
            min_input=1_000.0,
            max_input=50_000.0,
            steps=5,
        )
        assert isinstance(result, DualPunchCycleResult)
