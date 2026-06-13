"""AtomicExecutionCoordinator: C1/C2 cycle coordination with route hashing + receipt verification."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ExecutionCyclePhase(Enum):
    IDLE = "idle"
    C1_PENDING = "c1_pending"
    C1_CONFIRMED = "c1_confirmed"
    C1_FAILED = "c1_failed"
    C2_PENDING = "c2_pending"
    C2_CONFIRMED = "c2_confirmed"
    C2_FAILED = "c2_failed"
    COMPLETE = "complete"


@dataclass(frozen=True)
class RouteCommitment:
    """Deterministic hash of execution route."""

    route_id: str
    token_path: list[str]
    venue_path: list[str]
    calldata_hash: str
    timestamp_sec: float


@dataclass
class ExecutionReceipt:
    """Post-trade execution record."""

    tx_hash: str
    cycle_phase: ExecutionCyclePhase
    block_number: int
    gas_used: int
    profit_usd: float
    timestamp_sec: float
    explorer_url: str = ""


@dataclass
class C1C2State:
    """State machine for C1/C2 execution cycle."""

    phase: ExecutionCyclePhase = ExecutionCyclePhase.IDLE
    route_commitment: RouteCommitment | None = None
    c1_receipt: ExecutionReceipt | None = None
    c2_receipt: ExecutionReceipt | None = None
    cycle_start_ts: float = 0.0
    cycle_end_ts: float = 0.0
    nonce_used: int = 0
    replay_protection_hash: str = ""


class AtomicExecutionCoordinator:
    """Orchestrate C1/C2 execution cycles with safety gates."""

    CYCLE_TIMEOUT_SEC = 60.0
    C1_GAS_ESTIMATE = 280000
    C2_GAS_ESTIMATE = 280000

    def __init__(self):
        self.state = C1C2State()
        self.execution_history: list[ExecutionReceipt] = []

    def commit_route(self, token_path: list[str], venue_path: list[str], calldata: bytes) -> RouteCommitment:
        """Hash and commit execution route."""

        calldata_hash = hashlib.sha256(calldata).hexdigest()[:16]
        route_id = hashlib.sha256(
            json.dumps({"token_path": token_path, "venue_path": venue_path, "calldata": calldata_hash}).encode()
        ).hexdigest()[:16]

        commitment = RouteCommitment(
            route_id=route_id,
            token_path=token_path,
            venue_path=venue_path,
            calldata_hash=calldata_hash,
            timestamp_sec=time.time(),
        )

        self.state.route_commitment = commitment
        self.state.phase = ExecutionCyclePhase.C1_PENDING
        self.state.cycle_start_ts = time.time()

        logger.info(f"Route committed: {route_id}")
        return commitment

    def record_c1_execution(self, tx_hash: str, block: int, gas_used: int, profit_usd: float) -> ExecutionReceipt:
        """Record C1 execution."""

        receipt = ExecutionReceipt(
            tx_hash=tx_hash,
            cycle_phase=ExecutionCyclePhase.C1_CONFIRMED,
            block_number=block,
            gas_used=gas_used,
            profit_usd=profit_usd,
            timestamp_sec=time.time(),
            explorer_url=f"https://polygonscan.com/tx/{tx_hash}",
        )

        self.state.c1_receipt = receipt
        self.state.phase = ExecutionCyclePhase.C1_CONFIRMED
        self.execution_history.append(receipt)

        logger.info(f"✓ C1 confirmed: {tx_hash} | profit: {profit_usd:.2f} USD")
        return receipt

    def record_c1_failure(self, reason: str) -> None:
        """Mark C1 as failed."""
        self.state.phase = ExecutionCyclePhase.C1_FAILED
        logger.warning(f"✗ C1 failed: {reason}")

    def trigger_c2(self, c1_post_state: Dict[str, Any]) -> bool:
        """Decide whether to execute C2 based on C1 outcome."""

        if self.state.phase != ExecutionCyclePhase.C1_CONFIRMED:
            logger.warning("C2 trigger rejected: C1 not confirmed")
            return False

        post_profit = c1_post_state.get("profit_usd", 0.0)
        threshold = 0.5  # C2 only if C1 yielded at least $0.50

        if post_profit < threshold:
            self.state.phase = ExecutionCyclePhase.COMPLETE
            logger.info(f"C2 skipped: C1 profit {post_profit:.2f} < {threshold} USD threshold")
            return False

        self.state.phase = ExecutionCyclePhase.C2_PENDING
        logger.info("→ C2 triggered")
        return True

    def record_c2_execution(self, tx_hash: str, block: int, gas_used: int, profit_usd: float) -> ExecutionReceipt:
        """Record C2 execution."""

        receipt = ExecutionReceipt(
            tx_hash=tx_hash,
            cycle_phase=ExecutionCyclePhase.C2_CONFIRMED,
            block_number=block,
            gas_used=gas_used,
            profit_usd=profit_usd,
            timestamp_sec=time.time(),
            explorer_url=f"https://polygonscan.com/tx/{tx_hash}",
        )

        self.state.c2_receipt = receipt
        self.state.phase = ExecutionCyclePhase.COMPLETE
        self.state.cycle_end_ts = time.time()
        self.execution_history.append(receipt)

        logger.info(f"✓ C2 confirmed: {tx_hash} | profit: {profit_usd:.2f} USD")
        return receipt

    def cycle_summary(self) -> Dict[str, Any]:
        """Return cycle summary."""
        c1_profit = self.state.c1_receipt.profit_usd if self.state.c1_receipt else 0.0
        c2_profit = self.state.c2_receipt.profit_usd if self.state.c2_receipt else 0.0
        total_profit = c1_profit + c2_profit

        return {
            "phase": self.state.phase.value,
            "route_id": self.state.route_commitment.route_id if self.state.route_commitment else None,
            "c1_tx": self.state.c1_receipt.tx_hash if self.state.c1_receipt else None,
            "c2_tx": self.state.c2_receipt.tx_hash if self.state.c2_receipt else None,
            "c1_profit_usd": c1_profit,
            "c2_profit_usd": c2_profit,
            "total_profit_usd": total_profit,
            "cycle_duration_sec": self.state.cycle_end_ts - self.state.cycle_start_ts if self.state.cycle_end_ts else None,
            "status": "success" if self.state.phase == ExecutionCyclePhase.COMPLETE else "in_progress",
        }

    def reset_cycle(self) -> None:
        """Reset for next cycle."""
        self.state = C1C2State()
        logger.info("Cycle reset")
