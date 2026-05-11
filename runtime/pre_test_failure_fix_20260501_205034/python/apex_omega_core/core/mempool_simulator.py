from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MempoolImpact:
    original_output: float
    adjusted_output: float
    degradation_bps: float
    safe: bool


class MempoolSimulator:
    """Lightweight mempool impact model.

    This is a conservative degradation model until full pending-tx decoding is
    wired. It protects execution by applying a configurable haircut to the
    expected output and rejecting if degradation exceeds threshold.
    """

    def __init__(self, max_degradation_bps: float):
        self.max_degradation_bps = max_degradation_bps

    def evaluate(self, opportunity: Mapping[str, Any]) -> MempoolImpact:
        original = float(opportunity.get("expected_output", 0.0))
        if original <= 0:
            return MempoolImpact(0.0, 0.0, 0.0, False)

        # Conservative haircut (placeholder until real pending-tx simulation)
        haircut_bps = min(self.max_degradation_bps, 50.0)  # cap initial model
        adjusted = original * (1 - haircut_bps / 10_000.0)

        degradation_bps = ((original - adjusted) / original) * 10_000.0
        safe = degradation_bps <= self.max_degradation_bps

        return MempoolImpact(original, adjusted, degradation_bps, safe)

    def assert_safe(self, opportunity: Mapping[str, Any]) -> None:
        impact = self.evaluate(opportunity)
        if not impact.safe:
            raise ValueError(
                f"Mempool degradation too high: {impact.degradation_bps:.2f} bps"
            )
