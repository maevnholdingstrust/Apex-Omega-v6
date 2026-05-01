from types import SimpleNamespace

import pytest

from apex_omega_core.execution.fork_validator import ForkSimulationResult, validate_on_fork
from apex_omega_core.execution.p_exec_model import (
    ExecutionStats,
    PExecFeatures,
    calibrate_p_exec,
    model_p_exec,
    p_exec_calibrated,
    update_stats_after_attempt,
)
from apex_omega_core.execution.post_block_audit import PredictionErrorRollup, audit_post_block
from apex_omega_core.execution.route_builder import build_route, build_v2_route
from apex_omega_core.safety.execution_gates import reject_candidate, reject_executable_candidate
from apex_omega_core.strategies.dual_punch import DualPunchEngine
from apex_omega_core.v3.v3_pool_state import Q96, V3PoolState


def base_candidate(**overrides):
    data = dict(
        rpc_healthy=True,
        tvl_usd=100_000,
        reserve0=50_000,
        reserve1=50_000,
        reserves_verified=True,
        reserve_staleness_seconds=1,
        pool_type="V2",
        amount_in_usd=1_000,
        weakest_pool_tvl_usd=100_000,
        raw_spread_bps=100,
        expected_profit_usd=20,
        route_calldata=b"1234",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def base_v3_state():
    return V3PoolState(
        token0="0xToken0",
        token1="0xToken1",
        fee_bps=30,
        sqrt_price_x96=int(Q96),
        liquidity=1_000_000,
        tick=0,
        tick_spacing=60,
        decimals0=18,
        decimals1=18,
        pool_address="0xPool",
    )


def test_dust_pool_rejected():
    c = base_candidate(tvl_usd=10)
    assert reject_candidate(c) == "LOW_TVL"


def test_invalid_reserves_rejected():
    c = base_candidate(reserve0=0)
    assert reject_candidate(c) == "INVALID_RESERVES"


def test_unsafe_flash_size_rejected():
    c = base_candidate(amount_in_usd=10_000, weakest_pool_tvl_usd=100_000)
    assert reject_candidate(c) == "UNSAFE_FLASH_SIZE"


def test_v3_without_tick_validation_rejected():
    c = base_candidate(pool_type="V3", v3_state=base_v3_state(), tick_aware_quote_passed=False)
    assert reject_candidate(c) == "V3_NOT_VALIDATED"


def test_v3_without_state_rejected_even_if_flagged():
    c = base_candidate(pool_type="V3", v3_tick_validated=True, tick_aware_quote_passed=True)
    assert reject_candidate(c) == "V3_NOT_VALIDATED"


def test_v3_with_tick_aware_validation_passes_gate_without_pre_c2_fork_flag():
    c = base_candidate(
        pool_type="V3",
        v3_state=base_v3_state(),
        tick_aware_quote_passed=True,
        fork_sim_passed=False,
    )
    assert reject_candidate(c) is None


def test_v2_builder_cannot_build_v3_candidate():
    c = base_candidate(pool_type="V3", v3_state=base_v3_state(), tick_aware_quote_passed=True)
    with pytest.raises(ValueError, match="cannot build V3"):
        build_v2_route(c)


def test_v3_route_builder_rejects_without_tick_aware_validation():
    c = base_candidate(pool_type="V3", v3_state=base_v3_state(), tick_aware_quote_passed=False)
    with pytest.raises(ValueError, match="tick-aware validation"):
        build_route(c)


def test_v2_builder_stays_v2_for_v2_candidate():
    c = base_candidate(pool_type="V2")
    route = build_v2_route(c)
    assert route["pool_type"] == "V2"
    assert "v3_state" not in route


def test_missing_calldata_rejected():
    c = base_candidate(route_calldata=None)
    assert reject_candidate(c) == "MISSING_CALLDATA"


def test_absurd_spread_rejected():
    c = base_candidate(raw_spread_bps=100_000)
    assert reject_candidate(c) == "ABSURD_SPREAD"


def test_p_exec_calibration():
    stats = ExecutionStats(attempts=10, included=5)
    p = calibrate_p_exec(0.9, stats, calibration_weight=0.4)
    assert round(p, 2) == 0.74


def test_p_exec_model_uses_latency_relay_gas_mempool_complexity_revert_and_error():
    good = PExecFeatures(0.9, 1.0, 0, 0.1, 0.0, 1, 0.0, 0.0)
    bad = PExecFeatures(0.9, 0.5, 3, 1.0, 5.0, 4, 0.4, 500.0)
    assert model_p_exec(bad) < model_p_exec(good)


def test_p_exec_calibrated_blends_model_with_observed_inclusion_rate():
    features = PExecFeatures(0.9, 1.0, 0, 0, 0, 1, 0, 0)
    assert p_exec_calibrated(features, observed_inclusion_rate=0.5, calibration_weight=0.4) == pytest.approx(0.74)


def test_p_exec_updates_after_inclusion_revert_and_relay_events():
    stats = ExecutionStats()
    update_stats_after_attempt(
        stats,
        True,
        True,
        2,
        100,
        90,
        relay_success=False,
        model_p_exec_before=0.9,
    )
    assert stats.attempts == 1
    assert stats.inclusion_rate == 1
    assert stats.revert_rate == 1
    assert stats.avg_latency_blocks == 2
    assert stats.avg_prediction_error == 10
    assert stats.relay_success_rate == 0
    assert stats.last_calibrated_p_exec == pytest.approx(0.94)


def test_state_prediction_error_logged():
    stats = ExecutionStats()
    update_stats_after_attempt(stats, True, False, 1, 100, 95)
    assert stats.attempts == 1
    assert stats.inclusion_rate == 1
    assert stats.avg_prediction_error == 5


def test_payload_sim_mismatch_rejected():
    trade = SimpleNamespace(
        expected_out=100.0,
        expected_profit=10.0,
        min_profit=1.0,
        route_envelope=b"\x12\x34\x56\x78",
        fork_simulator=lambda calldata: ForkSimulationResult(success=True, final_out=90.0, profit=10.0),
    )
    ok, result = validate_on_fork(trade)
    assert not ok
    assert result.reason == "OUTPUT_MISMATCH"


def test_payload_sim_mismatch_rejected_by_executable_gate():
    c = base_candidate(expected_out=100.0)
    fork_result = SimpleNamespace(accepted=True, expected_out=100.0, simulated_out=90.0)
    assert reject_executable_candidate(c, fork_result) == "PAYLOAD_OUTPUT_MISMATCH"


def test_v2_constant_product_math_cannot_process_v3_route():
    engine = DualPunchEngine()
    route = [
        {
            "pool_type": "UNISWAP_V3",
            "venue": "uniswap_v3",
            "reserve_in": 1_000_000.0,
            "reserve_out": 1_000_000.0,
            "fee": 0.003,
        }
    ]
    with pytest.raises(ValueError, match="constant-product math cannot process V3"):
        engine.mutate_state(route, x1=1_000.0)


def test_post_block_audit_logs_prediction_error_and_adjusts_buffer():
    audit = audit_post_block(
        {"reserve0": 1_000, "reserve1": 2_000, "expected_out": 100, "expected_profit": 10},
        {"reserve0": 900, "reserve1": 2_000, "actual_out": 95, "realized_profit": 8},
    )
    rollup = PredictionErrorRollup(risk_buffer_bps=50, tighten_threshold_bps=75)
    new_buffer = rollup.record(audit)
    assert audit.prediction_error_bps > 75
    assert audit.as_log_record()["prediction_error_bps"] == pytest.approx(audit.prediction_error_bps)
    assert rollup.samples == 1
    assert rollup.avg_prediction_error_bps == pytest.approx(audit.prediction_error_bps)
    assert new_buffer > 50
