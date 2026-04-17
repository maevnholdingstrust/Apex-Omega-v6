"""
Tests for EVEngine (merged C1+C2) and RouteEnvelopeBuilder.
"""

import pytest

from apex_omega_core.strategies.ev_engine import EVEngine, MIN_PROFIT_USD, MIN_CONFIDENCE
from apex_omega_core.core.types import (
    ExecutionStats,
    ExecutableTrade,
    MempoolState,
    OpportunityInput,
)
from apex_omega_core.core.execution_compiler import (
    EnvelopeCompiler,
    ExecutionCompiler,
    RouteEnvelopeBuilder,
    RouteEnvelopeError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _healthy_route():
    """Two-hop route with very deep liquidity — keeps slippage well below the 40 bps gate."""
    return [
        {
            "venue": "uniswap",
            "pair": "USDC → TOKEN",
            "reserve_in": 50_000_000.0,
            "reserve_out": 51_500_000.0,
            "fee": 0.003,
            "price_in_usd": 1.0,
            "price_out_usd": 0.97,
            "tvl_usd": 40_000_000.0,
            "volume_24h_usd": 80_000_000.0,
            "age_in_blocks": 100,
        },
        {
            "venue": "sushiswap",
            "pair": "TOKEN → USDC",
            "reserve_in": 50_000_000.0,
            "reserve_out": 52_000_000.0,
            "fee": 0.003,
            "price_in_usd": 0.97,
            "price_out_usd": 1.0,
            "tvl_usd": 40_000_000.0,
            "volume_24h_usd": 70_000_000.0,
            "age_in_blocks": 200,
        },
    ]


def _shallow_route():
    """Two-hop route with very shallow pools that fail liquidity gates."""
    return [
        {
            "venue": "tiny_dex",
            "pair": "USDC → TOKEN",
            "reserve_in": 50.0,
            "reserve_out": 52.0,
            "fee": 0.003,
            "price_in_usd": 1.0,
            "price_out_usd": 0.97,
            "tvl_usd": 100.0,
            "volume_24h_usd": 50.0,
            "age_in_blocks": 10,
        },
        {
            "venue": "tiny_dex2",
            "pair": "TOKEN → USDC",
            "reserve_in": 50.0,
            "reserve_out": 52.0,
            "fee": 0.003,
            "price_in_usd": 0.97,
            "price_out_usd": 1.0,
            "tvl_usd": 100.0,
            "volume_24h_usd": 50.0,
            "age_in_blocks": 10,
        },
    ]


def _calm_mempool():
    return MempoolState(pending_tx_count=10, competing_arb_count=1, congestion_level=0.1, tip_drift_gwei=0.1)


def _congested_mempool():
    return MempoolState(pending_tx_count=5000, competing_arb_count=50, congestion_level=0.99, tip_drift_gwei=10.0)


def _good_stats():
    return ExecutionStats(historical_success_rate=0.9, next_block_inclusion_rate=0.75, avg_latency_ms=150.0, sample_count=500)


def _bad_stats():
    return ExecutionStats(historical_success_rate=0.05, next_block_inclusion_rate=0.10, avg_latency_ms=1500.0, sample_count=10)


# ---------------------------------------------------------------------------
# EVEngine tests
# ---------------------------------------------------------------------------

class TestEVEngine:
    def test_healthy_route_returns_executable_trade(self):
        engine = EVEngine()
        inp = OpportunityInput(
            route=_healthy_route(),
            gas_price_gwei=50.0,
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=10_000.0,
            max_input=500_000.0,
            raw_spread=0.03,
            optimize_steps=50,
        )
        trade = engine.evaluate(inp)
        assert trade is not None
        assert isinstance(trade, ExecutableTrade)
        assert trade.amount_in > 0
        assert trade.min_out > 0
        assert trade.ev > 0
        assert trade.p_exec > 0

    def test_shallow_route_rejected(self):
        engine = EVEngine()
        inp = OpportunityInput(
            route=_shallow_route(),
            gas_price_gwei=50.0,
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=1.0,
            max_input=40.0,
            raw_spread=0.04,
            optimize_steps=20,
        )
        # Shallow pools fail the liquidity gate in the optimizer → route pruned
        trade = engine.evaluate(inp)
        assert trade is None

    def test_zero_spread_route_rejected(self):
        """A route with identical reserves has no spread; EV should be negative."""
        engine = EVEngine(min_profit_usd=0.01)
        route = [
            {
                "venue": "uniswap",
                "pair": "USDC → USDC",
                "reserve_in": 1_000_000.0,
                "reserve_out": 1_000_000.0,
                "fee": 0.003,
                "price_in_usd": 1.0,
                "price_out_usd": 1.0,
                "tvl_usd": 800_000.0,
                "volume_24h_usd": 2_000_000.0,
                "age_in_blocks": 100,
            }
        ]
        inp = OpportunityInput(
            route=route,
            gas_price_gwei=50.0,
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=1_000.0,
            max_input=10_000.0,
            raw_spread=0.0,
            optimize_steps=20,
        )
        trade = engine.evaluate(inp)
        # No meaningful profit → should be rejected at net_profit or EV filter
        if trade is not None:
            assert trade.ev <= 0 or trade.net_profit <= 0

    def test_very_low_p_exec_rejected(self):
        """Very poor historical stats + extreme congestion should push p_exec below MIN_CONFIDENCE."""
        engine = EVEngine(min_profit_usd=0.001, min_confidence=0.9)
        inp = OpportunityInput(
            route=_healthy_route(),
            gas_price_gwei=1.0,   # extremely low tip → low gas_rank_score
            gas_estimate=350_000,
            mempool_state=_congested_mempool(),
            historical_stats=_bad_stats(),
            min_input=10_000.0,
            max_input=500_000.0,
            raw_spread=0.03,
            optimize_steps=20,
        )
        trade = engine.evaluate(inp)
        # With min_confidence=0.9 and all signals low, p_exec < 0.9 → rejected
        assert trade is None

    def test_p_exec_is_bounded(self):
        engine = EVEngine()
        inp = OpportunityInput(
            route=_healthy_route(),
            gas_price_gwei=200.0,  # very high tip
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=10_000.0,
            max_input=500_000.0,
            raw_spread=0.05,
            optimize_steps=20,
        )
        trade = engine.evaluate(inp)
        if trade is not None:
            assert 0.0 <= trade.p_exec <= 1.0

    def test_ev_engine_exposes_correct_fields(self):
        engine = EVEngine()
        inp = OpportunityInput(
            route=_healthy_route(),
            gas_price_gwei=50.0,
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=10_000.0,
            max_input=300_000.0,
            raw_spread=0.03,
            optimize_steps=30,
        )
        trade = engine.evaluate(inp)
        if trade is not None:
            assert hasattr(trade, "amount_in")
            assert hasattr(trade, "min_out")
            assert hasattr(trade, "expected_profit")
            assert hasattr(trade, "ev")
            assert hasattr(trade, "p_exec")
            assert hasattr(trade, "net_profit")
            assert hasattr(trade, "route")
            assert trade.route is not None

    def test_custom_thresholds_respected(self):
        """Raising min_profit_usd far above any realistic gain rejects the trade."""
        engine = EVEngine(min_profit_usd=1_000_000.0)
        inp = OpportunityInput(
            route=_healthy_route(),
            gas_price_gwei=50.0,
            gas_estimate=350_000,
            mempool_state=_calm_mempool(),
            historical_stats=_good_stats(),
            min_input=10_000.0,
            max_input=300_000.0,
            raw_spread=0.03,
            optimize_steps=30,
        )
        trade = engine.evaluate(inp)
        assert trade is None


# ---------------------------------------------------------------------------
# RouteEnvelopeBuilder tests
# ---------------------------------------------------------------------------

ROUTER_A = "0x1111111111111111111111111111111111111111"
ROUTER_B = "0x2222222222222222222222222222222222222222"
TOKEN_IN  = "0x3333333333333333333333333333333333333333"
TOKEN_MID = "0x4444444444444444444444444444444444444444"
TOKEN_OUT = "0x5555555555555555555555555555555555555555"


def _two_step_route():
    """Minimal valid two-hop route_steps list with enough margin to pass the profitability guard.

    Final step expected_out (102_000) gives cascade_min = 102_000 * (1 - buffer) ≥ 101_000
    which exceeds flashloan_amount (100_000) + min_profit (500) = 100_500.
    """
    return [
        {
            "protocol": 1,
            "target": ROUTER_A,
            "approve_token": TOKEN_IN,
            "output_token": TOKEN_MID,
            "call_value": 0,
            "expected_out": 99_000.0,
            "fee_bps": 30,
            "data": b"\x12\x34",
        },
        {
            "protocol": 2,
            "target": ROUTER_B,
            "approve_token": TOKEN_MID,
            "output_token": TOKEN_OUT,
            "call_value": 0,
            "expected_out": 102_000.0,
            "fee_bps": 30,
            "data": b"\xab\xcd",
        },
    ]


class TestRouteEnvelopeBuilder:
    def test_valid_envelope_builds_successfully(self):
        builder = RouteEnvelopeBuilder(
            known_routers={ROUTER_A, ROUTER_B},
            profit_token=TOKEN_OUT,
        )
        envelope = builder.build(
            amount_in=100_000.0,
            min_out=100_500.0,
            route_steps=_two_step_route(),
            min_profit=500.0,
            flashloan_amount=100_000.0,
        )
        assert envelope is not None
        assert len(envelope.steps) == 2
        assert envelope.amount_in == 100_000
        assert envelope.min_profit == 500

    def test_token_consistency_guard_fires(self):
        """Mismatched token between step 1 output and step 2 approve raises guard 1."""
        steps = _two_step_route()
        steps[1]["approve_token"] = TOKEN_OUT  # wrong: should be TOKEN_MID
        builder = RouteEnvelopeBuilder(known_routers={ROUTER_A, ROUTER_B})
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 100_500.0, steps, 500.0, 100_000.0)
        assert exc_info.value.guard == "TOKEN_CONSISTENCY"

    def test_empty_data_guard_fires(self):
        """Empty bytes in step data raises guard 4."""
        steps = _two_step_route()
        steps[0]["data"] = b""
        builder = RouteEnvelopeBuilder(known_routers={ROUTER_A, ROUTER_B})
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 100_500.0, steps, 500.0, 100_000.0)
        assert exc_info.value.guard == "DATA_NON_EMPTY"

    def test_unknown_router_guard_fires(self):
        """A target not in known_routers raises guard 5."""
        builder = RouteEnvelopeBuilder(known_routers={"0x9999999999999999999999999999999999999999"})
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 100_500.0, _two_step_route(), 500.0, 100_000.0)
        assert exc_info.value.guard == "TARGET_VALIDATION"

    def test_unknown_router_skipped_when_no_whitelist(self):
        """When known_routers is empty, target validation is skipped."""
        builder = RouteEnvelopeBuilder(known_routers=set())
        envelope = builder.build(100_000.0, 100_500.0, _two_step_route(), 500.0, 100_000.0)
        assert len(envelope.steps) == 2

    def test_zero_amount_in_guard_fires(self):
        builder = RouteEnvelopeBuilder()
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(0.0, 100_500.0, _two_step_route(), 500.0, 100_000.0)
        assert exc_info.value.guard == "FLASHLOAN_SAFETY"

    def test_zero_min_profit_guard_fires(self):
        builder = RouteEnvelopeBuilder()
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 100_500.0, _two_step_route(), 0.0, 100_000.0)
        assert exc_info.value.guard == "FLASHLOAN_SAFETY"

    def test_empty_steps_guard_fires(self):
        builder = RouteEnvelopeBuilder()
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 100_500.0, [], 500.0, 100_000.0)
        assert exc_info.value.guard == "FLASHLOAN_SAFETY"

    def test_profitability_guard_fires_when_min_out_too_low(self):
        """If final min_amount_out < flashloan + min_profit, guard 3 fires."""
        steps = _two_step_route()
        # Clear expected_out from the final step so the builder falls back to
        # the global min_out (set to 0.5 here → cascade_min=1), which is far
        # below flashloan_amount (100_000) + min_profit (500) = 100_500.
        steps[-1]["expected_out"] = 0.0
        builder = RouteEnvelopeBuilder()
        with pytest.raises(RouteEnvelopeError) as exc_info:
            builder.build(100_000.0, 0.5, steps, 500.0, 100_000.0)
        assert exc_info.value.guard == "PROFITABILITY"

    def test_cascade_min_out_applied_per_step(self):
        """Each step's minAmountOut should reflect the per-step risk buffer."""
        builder = RouteEnvelopeBuilder(step_risk_buffer=0.01)
        envelope = builder.build(
            amount_in=100_000.0,
            min_out=100_500.0,
            route_steps=_two_step_route(),
            min_profit=500.0,
            flashloan_amount=100_000.0,
        )
        # Step 0: expected_out=99_000, buffer=1% → min≈98_010
        step0_min = envelope.steps[0]["minAmountOut"]
        assert step0_min == pytest.approx(99_000 * 0.99, abs=1)

    def test_to_compiler_input_produces_valid_dict(self):
        builder = RouteEnvelopeBuilder(profit_token=TOKEN_OUT)
        envelope = builder.build(
            100_000.0, 100_500.0, _two_step_route(), 500.0, 100_000.0
        )
        compiler_input = builder.to_compiler_input(envelope)
        assert compiler_input["asset"] == TOKEN_OUT
        assert compiler_input["min_profit"] == 500
        assert len(compiler_input["steps"]) == 2

    def test_envelope_compatible_with_execution_compiler(self):
        """BuiltEnvelope → to_compiler_input → ExecutionCompiler → non-empty bytes."""
        builder = RouteEnvelopeBuilder(profit_token=TOKEN_OUT)
        envelope = builder.build(
            100_000.0, 100_500.0, _two_step_route(), 500.0, 100_000.0
        )
        compiler_input = builder.to_compiler_input(envelope)
        # Patch outputToken into steps (institutional format requires it)
        for i, step in enumerate(compiler_input["steps"]):
            if "outputToken" not in step:
                step["outputToken"] = TOKEN_OUT

        compiler = ExecutionCompiler()
        compiled = compiler.compile_for_institutional(compiler_input)
        assert isinstance(compiled.encoded_payload, bytes)
        assert len(compiled.encoded_payload) > 0
        assert compiled.min_profit == 500
