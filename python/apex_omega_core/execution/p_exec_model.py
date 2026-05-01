from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionStats:
    attempts: int = 0
    included: int = 0
    reverts: int = 0
    relay_successes: int = 0
    relay_failures: int = 0
    total_latency_blocks: float = 0.0
    total_prediction_error: float = 0.0
    last_model_p_exec: float = 0.0
    last_calibrated_p_exec: float = 0.0

    @property
    def inclusion_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.included / self.attempts

    @property
    def revert_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.reverts / self.attempts

    @property
    def avg_latency_blocks(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.total_latency_blocks / self.attempts

    @property
    def avg_prediction_error(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.total_prediction_error / self.attempts

    @property
    def relay_success_rate(self) -> float:
        total = self.relay_successes + self.relay_failures
        if total <= 0:
            return 1.0
        return self.relay_successes / total


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def blend(model_p_exec: float, observed_inclusion_rate: float, calibration_weight: float = 0.40) -> float:
    model = clamp01(model_p_exec)
    observed = clamp01(observed_inclusion_rate)
    w = clamp01(calibration_weight)
    return clamp01(((1.0 - w) * model) + (w * observed))


def calibrate_p_exec(model_p: float, stats: ExecutionStats, calibration_weight: float = 0.40) -> float:
    return blend(model_p, stats.inclusion_rate, calibration_weight)


@dataclass(frozen=True)
class PExecFeatures:
    historical_inclusion_rate: float
    relay_success_rate: float
    bundle_latency_blocks: float
    gas_percentile_rank: float
    mempool_density: float
    route_complexity: float
    recent_revert_rate: float
    prediction_error_bps: float


def model_p_exec(features: PExecFeatures) -> float:
    p = clamp01(features.historical_inclusion_rate)
    p *= clamp01(features.relay_success_rate)
    p *= 1.0 / (1.0 + max(0.0, features.bundle_latency_blocks) * 0.35)
    p *= 1.0 - (clamp01(features.gas_percentile_rank) * 0.25)
    p *= 1.0 / (1.0 + max(0.0, features.mempool_density) * 0.20)
    p *= 1.0 / (1.0 + max(0.0, features.route_complexity - 1.0) * 0.12)
    p *= 1.0 - clamp01(features.recent_revert_rate)
    p *= 1.0 / (1.0 + max(0.0, features.prediction_error_bps) / 1_000.0)
    return clamp01(p)


def p_exec_calibrated(
    features: PExecFeatures,
    observed_inclusion_rate: float,
    calibration_weight: float = 0.40,
) -> float:
    return blend(model_p_exec(features), observed_inclusion_rate, calibration_weight)


def update_stats_after_attempt(
    stats: ExecutionStats,
    included: bool,
    reverted: bool,
    latency_blocks: float,
    expected_out: float,
    actual_out: float,
    relay_success: bool = True,
    model_p_exec_before: Optional[float] = None,
    calibration_weight: float = 0.40,
) -> ExecutionStats:
    stats.attempts += 1
    stats.included += 1 if included else 0
    stats.reverts += 1 if reverted else 0
    stats.relay_successes += 1 if relay_success else 0
    stats.relay_failures += 0 if relay_success else 1
    stats.total_latency_blocks += max(0.0, float(latency_blocks))
    stats.total_prediction_error += abs(float(expected_out) - float(actual_out))
    if model_p_exec_before is not None:
        stats.last_model_p_exec = clamp01(model_p_exec_before)
        stats.last_calibrated_p_exec = calibrate_p_exec(
            stats.last_model_p_exec,
            stats,
            calibration_weight=calibration_weight,
        )
    return stats
