from types import SimpleNamespace

from apex_omega_core.safety.execution_gates import reject_candidate
from apex_omega_core.execution.p_exec_model import ExecutionStats, calibrate_p_exec, update_stats_after_attempt


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
    c = base_candidate(pool_type="V3", v3_tick_validated=False)
    assert reject_candidate(c) == "V3_NOT_VALIDATED"


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


def test_state_prediction_error_logged():
    stats = ExecutionStats()
    update_stats_after_attempt(stats, True, False, 1, 100, 95)
    assert stats.attempts == 1
    assert stats.inclusion_rate == 1
    assert stats.avg_prediction_error == 5
