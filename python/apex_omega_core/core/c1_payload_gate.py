"""Strict validation gate for C1 VM payloads."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .executable_payloads import FlashloanIntegratedC1Payload, STATUS_EXECUTABLE, STATUS_LOCKED


@dataclass
class GateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if not self.passed:
            raise ValueError("C1 payload validation failed: " + " | ".join(self.violations))


@dataclass(frozen=True)
class C1GateConfig:
    min_net_profit_usd: float = 5.0
    current_block: Optional[int] = None
    expected_target_contract: Optional[str] = None


class C1PayloadGate:
    """Deterministic final validation before VM calldata construction."""

    def __init__(self, config: C1GateConfig | None = None) -> None:
        self.config = config or C1GateConfig()

    @staticmethod
    def _same_addr(a: Any, b: Any) -> bool:
        return isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower()

    def validate(
        self,
        payload: FlashloanIntegratedC1Payload,
        redis_record: Mapping[str, Any] | None,
        *,
        built_calldata_hash: str | None = None,
    ) -> GateResult:
        violations: list[str] = []

        try:
            payload.assert_route_invariants()
        except Exception as exc:
            violations.append(str(exc))

        if self.config.expected_target_contract and not self._same_addr(
            payload.targetContract, self.config.expected_target_contract
        ):
            violations.append("targetContract mismatch")

        if self.config.current_block is not None and int(payload.deadlineBlock) < int(self.config.current_block):
            violations.append("candidate expired")

        if float(payload.netProfitUsd) < float(self.config.min_net_profit_usd):
            violations.append(f"netProfitUsd {payload.netProfitUsd} < minimum {self.config.min_net_profit_usd}")

        if redis_record is None:
            violations.append("opportunity lock record missing")
        else:
            status = redis_record.get("status") or redis_record.get("lockStatus")
            candidate = redis_record.get("candidateStatus")
            if status != STATUS_LOCKED:
                violations.append(f"lock status must be {STATUS_LOCKED}, got {status!r}")
            if candidate != STATUS_EXECUTABLE:
                violations.append(f"candidateStatus must be {STATUS_EXECUTABLE}, got {candidate!r}")
            if str(redis_record.get("redisId") or "") != str(payload.redisId):
                violations.append("redisId mismatch")
            if not self._same_addr(redis_record.get("flashloanAsset"), payload.flashloanAsset):
                violations.append("flashloanAsset mismatch")
            if int(redis_record.get("flashloanAmount") or -1) != int(payload.flashloanAmount):
                violations.append("flashloanAmount mismatch")
            expected_hash = redis_record.get("opportunityHash")
            if expected_hash and str(expected_hash).lower() != payload.opportunity_hash().lower():
                violations.append("opportunityHash mismatch")

        if built_calldata_hash:
            if not payload.calldataHash:
                violations.append("payload calldataHash missing")
            elif str(payload.calldataHash).lower() != str(built_calldata_hash).lower():
                violations.append("calldataHash mismatch")

        return GateResult(passed=not violations, violations=violations)
