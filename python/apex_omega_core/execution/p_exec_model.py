from dataclasses import dataclass


@dataclass
class ExecutionStats:
    attempts: int = 0
    included: int = 0
    reverts: int = 0
    total_latency_blocks: float = 0.0
    total_prediction_error: float = 0.0

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


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def blend(model_p_exec: float, observed_inclusion_rate: float, calibration_weight: float = 0.40) -> float:
    model = clamp01(model_p_exec)
    observed = clamp01(observed_inclusion_rate)
    w = clamp01(calibration_weight)
    return clamp01(((1.0 - w) * model) + (w * observed))


def calibrate_p_exec(model_p: float, stats: ExecutionStats, calibration_weight: float = 0.40) -> float:
    return blend(model_p, stats.inclusion_rate, calibration_weight)


def update_stats_after_attempt(
    stats: ExecutionStats,
    included: bool,
    reverted: bool,
    latency_blocks: float,
    expected_out: float,
    actual_out: float,
) -> ExecutionStats:
    stats.attempts += 1
    stats.included += 1 if included else 0
    stats.reverts += 1 if reverted else 0
    stats.total_latency_blocks += max(0.0, float(latency_blocks))
    stats.total_prediction_error += abs(float(expected_out) - float(actual_out))
    return stats
