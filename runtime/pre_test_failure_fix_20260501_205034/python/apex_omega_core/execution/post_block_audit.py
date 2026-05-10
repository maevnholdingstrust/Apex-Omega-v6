from dataclasses import dataclass
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class PostBlockAudit:
    predicted_reserve0: float
    predicted_reserve1: float
    actual_reserve0: float
    actual_reserve1: float
    expected_out: float
    actual_out: float
    expected_profit: float
    realized_profit: float

    @property
    def reserve_error_bps(self) -> float:
        p0 = max(abs(self.predicted_reserve0), 1.0)
        p1 = max(abs(self.predicted_reserve1), 1.0)
        e0 = abs(self.predicted_reserve0 - self.actual_reserve0) / p0
        e1 = abs(self.predicted_reserve1 - self.actual_reserve1) / p1
        return max(e0, e1) * 10_000.0

    @property
    def output_error_bps(self) -> float:
        return abs(self.expected_out - self.actual_out) / max(abs(self.expected_out), 1.0) * 10_000.0

    @property
    def profit_error_bps(self) -> float:
        return abs(self.expected_profit - self.realized_profit) / max(abs(self.expected_profit), 1.0) * 10_000.0

    @property
    def prediction_error_bps(self) -> float:
        return max(self.reserve_error_bps, self.output_error_bps, self.profit_error_bps)

    def as_log_record(self) -> Dict[str, float]:
        return {
            "predicted_reserve0": self.predicted_reserve0,
            "predicted_reserve1": self.predicted_reserve1,
            "actual_reserve0": self.actual_reserve0,
            "actual_reserve1": self.actual_reserve1,
            "expected_out": self.expected_out,
            "actual_out": self.actual_out,
            "expected_profit": self.expected_profit,
            "realized_profit": self.realized_profit,
            "reserve_error_bps": self.reserve_error_bps,
            "output_error_bps": self.output_error_bps,
            "profit_error_bps": self.profit_error_bps,
            "prediction_error_bps": self.prediction_error_bps,
        }


@dataclass
class PredictionErrorRollup:
    samples: int = 0
    total_error_bps: float = 0.0
    risk_buffer_bps: float = 50.0
    tighten_threshold_bps: float = 75.0
    loosen_threshold_bps: float = 20.0
    adjustment_bps: float = 5.0

    @property
    def avg_prediction_error_bps(self) -> float:
        if self.samples <= 0:
            return 0.0
        return self.total_error_bps / self.samples

    def record(self, audit: PostBlockAudit) -> float:
        self.samples += 1
        self.total_error_bps += audit.prediction_error_bps
        if audit.prediction_error_bps > self.tighten_threshold_bps:
            self.risk_buffer_bps += self.adjustment_bps
        elif audit.prediction_error_bps < self.loosen_threshold_bps:
            self.risk_buffer_bps = max(0.0, self.risk_buffer_bps - self.adjustment_bps)
        return self.risk_buffer_bps


def _get(mapping: Mapping[str, Any], name: str, default: float = 0.0) -> float:
    return float(mapping.get(name, default))


def audit_post_block(predicted: Mapping[str, Any], actual: Mapping[str, Any]) -> PostBlockAudit:
    return PostBlockAudit(
        predicted_reserve0=_get(predicted, "reserve0"),
        predicted_reserve1=_get(predicted, "reserve1"),
        actual_reserve0=_get(actual, "reserve0"),
        actual_reserve1=_get(actual, "reserve1"),
        expected_out=_get(predicted, "expected_out"),
        actual_out=_get(actual, "actual_out"),
        expected_profit=_get(predicted, "expected_profit", _get(predicted, "expected_profit_usd")),
        realized_profit=_get(actual, "realized_profit", _get(actual, "realized_profit_usd")),
    )
