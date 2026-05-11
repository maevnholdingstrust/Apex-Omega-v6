from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ForkReadinessStatus(str, Enum):
    READY_FOR_FORK_SIM = "READY_FOR_FORK_SIM"
    BLOCKED_BY_CONFIG = "BLOCKED_BY_CONFIG"
    BLOCKED_BY_ROUTE = "BLOCKED_BY_ROUTE"
    BLOCKED_BY_PAYLOAD = "BLOCKED_BY_PAYLOAD"
    BLOCKED_BY_SIMULATION = "BLOCKED_BY_SIMULATION"


@dataclass(frozen=True)
class ForkReadinessReport:
    status: ForkReadinessStatus
    ready: bool
    blockers: tuple[str, ...]
    notes: tuple[str, ...]


def fork_simulation_readiness_gate(
    *,
    fork_rpc_url: str | None,
    route_execution_grade: bool,
    payload: bytes | None,
    expected_profit_usdc: float,
    required_env_names: Iterable[str] = ("FORK_RPC_URL", "C1_EXECUTOR_ADDRESS"),
) -> ForkReadinessReport:
    """Fail-closed readiness gate before any fork simulation or live execution.

    This gate does not sign, submit, or relay transactions. It only answers
    whether the system is configured enough to run a local fork simulation.
    """
    blockers: list[str] = []
    notes: list[str] = []

    if not fork_rpc_url:
        blockers.append("missing FORK_RPC_URL / local fork endpoint")
    if not route_execution_grade:
        blockers.append("route is not execution-grade")
    if not payload:
        blockers.append("missing compiled payload")
    if expected_profit_usdc <= 0:
        blockers.append("expected_profit_usdc must be positive before fork sim")

    for name in required_env_names:
        notes.append(f"required_env:{name}")

    if blockers:
        if any("route" in b for b in blockers):
            status = ForkReadinessStatus.BLOCKED_BY_ROUTE
        elif any("payload" in b for b in blockers):
            status = ForkReadinessStatus.BLOCKED_BY_PAYLOAD
        else:
            status = ForkReadinessStatus.BLOCKED_BY_CONFIG
        return ForkReadinessReport(status, False, tuple(blockers), tuple(notes))

    return ForkReadinessReport(
        ForkReadinessStatus.READY_FOR_FORK_SIM,
        True,
        tuple(),
        tuple(notes + ["safe: no signing or broadcasting performed"]),
    )
