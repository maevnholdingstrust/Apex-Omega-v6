"""Cycle Orchestrator — C1 → C2 sequenced execution with full gate enforcement.

Laws
----
1.  C1 executes first.  C2 never runs before C1 has a confirmed receipt.
2.  C2 operates on POST-C1 state.  Pre-C1 state is discarded after C1 receipt.
3.  Spread ≠ profit.  Every path that touches ``amount_in`` must also carry
    ``expected_out``, ``min_out``, ``flash_fee``, ``gas_cost``, ``dex_fees``,
    and ``net_profit``.  Those six numbers gate every send.
4.  Fork-sim before every send (C1 AND C2).
5.  C1 envelope is NEVER passed to C2.  C2 envelope is NEVER passed to C1.
6.  C2 may MIRROR, REVERSE, or DO_NOTHING.  DO_NOTHING is the safe default.
7.  Every cycle emits exactly one SSN (Session Sequence Number) ID.
8.  Merkle proof correctness ≠ route safety.  Net profit gate is enforced
    *after* the Merkle root is verified, not instead of it.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# C2 decision enum
# ---------------------------------------------------------------------------

class C2Decision(str, Enum):
    MIRROR = "MIRROR"
    REVERSE = "REVERSE"
    DO_NOTHING = "DO_NOTHING"


# ---------------------------------------------------------------------------
# Execution math carrier — spread ≠ profit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionMath:
    """All six numbers required before every send.

        Principal and output values must be positive. Costs may be zero, but
        never negative. ``net_profit`` may be conservative, but it may not
        exceed the value implied by the quoted output and declared costs.

    Attributes
    ----------
    amount_in:
        Flashloan principal in the loan asset's native units.
    expected_out:
        AMM-quoted final output (same asset as ``amount_in``).
    min_out:
        Slippage-protected minimum output.  TX reverts if output < min_out.
    flash_fee:
        Flashloan provider fee in the loan asset's native units.
    gas_cost:
        Estimated gas cost converted to the loan asset.
    dex_fees:
        Sum of all DEX swap fees across both legs.
    net_profit:
        ``expected_out - amount_in - flash_fee - gas_cost - dex_fees``.
        Must be > ``min_profit_usd`` threshold or the send is aborted.
    """

    amount_in: float
    expected_out: float
    min_out: float
    flash_fee: float
    gas_cost: float
    dex_fees: float
    net_profit: float

    def is_viable(self, min_profit: float = 0.0) -> bool:
        """Return True iff net_profit exceeds the threshold and invariants hold."""
        values = (
            self.amount_in,
            self.expected_out,
            self.min_out,
            self.flash_fee,
            self.gas_cost,
            self.dex_fees,
            self.net_profit,
            min_profit,
        )
        if not all(math.isfinite(value) for value in values):
            return False
        declared_costs = self.flash_fee + self.gas_cost + self.dex_fees
        maximum_net_profit = self.expected_out - self.amount_in - declared_costs
        profitable_floor = self.amount_in + declared_costs + min_profit
        return (
            self.amount_in > 0
            and self.expected_out > self.amount_in
            and 0 < self.min_out <= self.expected_out
            and self.flash_fee >= 0
            and self.gas_cost >= 0
            and self.dex_fees >= 0
            and self.net_profit <= maximum_net_profit
            and self.net_profit > min_profit
            and self.min_out > profitable_floor
        )

    def assert_viable(self, min_profit: float = 0.0) -> None:
        if not self.is_viable(min_profit):
            raise ValueError(
                f"ExecutionMath not viable: net_profit={self.net_profit:.6f} "
                f"(<= threshold {min_profit:.6f}), "
                f"amount_in={self.amount_in:.4f}, expected_out={self.expected_out:.4f}"
            )


# ---------------------------------------------------------------------------
# Opportunity fingerprint (audit visibility — NOT the execution decision)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpportunityFingerprint:
    """Discovery-time audit values.

    These are AUDIT fields.  They describe what the scanner observed.
    They do NOT govern execution — ``ExecutionMath`` does.

    Required fields
    ---------------
    leg1_buy_price:
        Lowest executable buy price (proven by BestBuyProof).
    leg2_sell_price:
        Highest executable sell price.
    raw_spread_bps:
        ``(sell - buy) / buy * 10_000`` — raw, before costs.

    Optional audit fields (populated by OpportunityAdapter)
    --------------------------------------------------------
    leg1_dex:
        Human-readable DEX name for leg 1, e.g. ``"QuickSwap V2"``.
    leg1_pool:
        Checksum pool address used for leg 1.
    leg2_dex:
        Human-readable DEX name for leg 2.
    leg2_pool:
        Checksum pool address used for leg 2.
    protocol_family_leg1:
        PoolFamily string, e.g. ``"V2_CPMM"``.
    protocol_family_leg2:
        PoolFamily string, e.g. ``"V3_CLMM"``.
    """

    leg1_buy_price: float   # lowest executable buy price (proven by BestBuyProof)
    leg2_sell_price: float  # highest executable sell price
    raw_spread_bps: float   # (sell - buy) / buy * 10_000 — raw, before costs

    # Optional audit fields — filled by OpportunityAdapter, empty in unit tests
    leg1_dex: str = ""
    leg1_pool: str = ""
    leg2_dex: str = ""
    leg2_pool: str = ""
    protocol_family_leg1: str = ""
    protocol_family_leg2: str = ""

    def __post_init__(self) -> None:
        if self.leg1_buy_price <= 0:
            raise ValueError("fingerprint.leg1_buy_price must be > 0")
        if self.leg2_sell_price <= 0:
            raise ValueError("fingerprint.leg2_sell_price must be > 0")
        if self.raw_spread_bps <= 0:
            raise ValueError("fingerprint.raw_spread_bps must be > 0")


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------

@dataclass
class Opportunity:
    """An arbitrage opportunity emitted by the discovery engine.

    Fields
    ------
    fingerprint:
        Audit-only discovery values.  Not used in execution math.
    exec_math:
        The six numbers that govern whether to send.
    target_pools:
        Addresses of all pools involved.  Used by StateMirror to reload.
    gross_profit_usd:
        Gross USD profit estimate (before costs).
    net_profit_usd:
        Net USD profit after all costs.  May differ from ``exec_math.net_profit``
        if the loan asset != USD.
    min_profit_usd:
        Minimum acceptable net profit.  DO_NOTHING if below.
    """

    fingerprint: OpportunityFingerprint
    exec_math: ExecutionMath
    target_pools: List[str]
    gross_profit_usd: float = 0.0
    net_profit_usd: float = 0.0
    min_profit_usd: float = 2.0
    pair_id: str = ""
    lane_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Fork simulation result (thin interface; real impl lives in fork_validator.py)
# ---------------------------------------------------------------------------

@dataclass
class ForkSimResult:
    ok: bool
    net_profit_usd: float = 0.0
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# C2 candidate (MIRROR or REVERSE) with its own execution math
# ---------------------------------------------------------------------------

@dataclass
class C2Candidate:
    kind: C2Decision
    params: Any                 # strategy-specific params for Merkle encoding
    exec_math: ExecutionMath
    min_profit_usd: float = 2.0
    pair_id: str = ""
    lane_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Cycle result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CycleResult:
    """Immutable record of one completed cycle.

    Attributes
    ----------
    cycle_id:
        Unique SSN — one per cycle, logged exactly once.
    c1_tx_hash:
        Transaction hash from C1 execution.  ``None`` if C1 aborted.
    c2_tx_hash:
        Transaction hash from C2 execution.  ``None`` if C2 chose DO_NOTHING.
    c2_decision:
        The C2 branch taken.
    gross_profit_usd:
        Opportunity gross profit at discovery time.
    net_profit_usd:
        Realized net profit (0.0 if DO_NOTHING).
    raw_spread_bps:
        Audit-only spread from the discovery fingerprint.
    status:
        Human-readable terminal status code.
    """

    cycle_id: str
    c1_tx_hash: Optional[str]
    c2_tx_hash: Optional[str]
    c2_decision: C2Decision
    gross_profit_usd: float
    net_profit_usd: float
    raw_spread_bps: float
    status: str
    pair_id: str = ""
    lane_id: Optional[int] = None
    c1_inclusion_block: Optional[int] = None
    c2_expires_at_block: Optional[int] = None


@dataclass(frozen=True)
class PendingC2Hold:
    """Post-C1 hold consumed by C2 independently of later scanner cycles."""

    cycle_id: str
    opportunity: Opportunity
    c1_tx_hash: str
    c1_inclusion_block: int
    expires_at_block: int
    pair_id: str
    lane_id: Optional[int]


# ---------------------------------------------------------------------------
# Dependency protocols (constructor-injected, easy to mock in tests)
# ---------------------------------------------------------------------------

@runtime_checkable
class ForkValidator(Protocol):
    async def validate_c1(
        self,
        *,
        cycle_id: str,
        opportunity: Opportunity,
        payload: Any,
    ) -> ForkSimResult: ...

    async def validate_c2(
        self,
        *,
        cycle_id: str,
        post_c1_state: Any,
        payload: Any,
        decision: C2Candidate,
    ) -> ForkSimResult: ...


@runtime_checkable
class StateMirror(Protocol):
    async def reload_target_pools(
        self,
        *,
        pools: List[str],
        min_block: int,
    ) -> Any: ...


@runtime_checkable
class C1Executor(Protocol):
    def build_payload(self, opportunity: Opportunity) -> Any: ...
    async def execute(self, payload: Any) -> str: ...
    async def wait_for_flash_arb_executed(self, *, tx_hash: str) -> Any: ...


@runtime_checkable
class C2Executor(Protocol):
    def build_payload(self, *, candidate: C2Candidate, proof: Any) -> Any: ...
    async def update_merkle_root(self, root: bytes) -> None: ...
    async def execute(self, payload: Any) -> str: ...


@runtime_checkable
class C2DecisionEngine(Protocol):
    def build_candidates(
        self,
        *,
        original_opportunity: Opportunity,
        post_c1_state: Any,
    ) -> List[C2Candidate]: ...

    def select_best_candidate(
        self,
        candidates: List[C2Candidate],
    ) -> C2Candidate: ...


@runtime_checkable
class MerkleService(Protocol):
    def build_root_and_proof(
        self,
        *,
        params: Any,
        all_candidates: List[Any],
    ) -> tuple[bytes, Any]: ...


@runtime_checkable
class CycleLogger(Protocol):
    def new_cycle_id(self, opportunity: Opportunity) -> str: ...
    def reject(self, cycle_id: str, opportunity: Opportunity, reason: str) -> None: ...
    def finalize(self, result: CycleResult) -> None: ...


# ---------------------------------------------------------------------------
# CycleOrchestrator
# ---------------------------------------------------------------------------

class CycleOrchestrator:
    """Sequences C1 → C2 with enforced gates at every transition.

    Injection contract
    ------------------
    All dependencies are injected at construction; none are created internally.
    This makes the orchestrator trivially testable via fakes/stubs.

    Envelope isolation
    ------------------
    ``c1_executor.build_payload`` produces a C1 envelope.
    ``c2_executor.build_payload`` produces a C2 envelope.
    These objects NEVER cross the C1/C2 boundary — the orchestrator never
    passes a C1 payload to any C2 method or vice versa.

    Post-C1 state
    -------------
    After ``c1_executor.wait_for_flash_arb_executed``, the orchestrator calls
    ``state_mirror.reload_target_pools`` with ``min_block = c1_receipt.block_number``
    to force a fresh chain read.  C2 NEVER sees pre-C1 state.

    C2 hold lifecycle
    -----------------
    ``trigger_c1`` and ``resolve_c2_hold`` are deliberately separate.  A
    scanner can continue producing later C1 cycles while earlier C2 holds wait
    for confirmation and post-C1 evaluation.  A hold expires five blocks after
    C1 inclusion and remains locked to the triggering pair and lane.
    """

    C2_MAX_BLOCK_LIFETIME = 5

    def __init__(
        self,
        discovery_engine: Any,
        state_mirror: StateMirror,
        c1_executor: C1Executor,
        c2_executor: C2Executor,
        fork_validator: ForkValidator,
        c2_decision_engine: C2DecisionEngine,
        merkle_service: MerkleService,
        cycle_logger: CycleLogger,
        lane_manager: Any = None,
    ) -> None:
        self.discovery_engine = discovery_engine
        self.state_mirror = state_mirror
        self.c1_executor = c1_executor
        self.c2_executor = c2_executor
        self.fork_validator = fork_validator
        self.c2_decision_engine = c2_decision_engine
        self.merkle_service = merkle_service
        self.cycle_logger = cycle_logger
        self.lane_manager = lane_manager
        self.pending_c2_holds: dict[str, PendingC2Hold] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_cycle(self, opportunity: Opportunity) -> CycleResult:
        """Compatibility wrapper that resolves one C1-triggered C2 hold inline.

        Runtime scanners should call :meth:`trigger_c1` and schedule
        :meth:`resolve_c2_hold` independently so later C1 cycles are not
        blocked by an earlier C2 hold.
        """
        triggered = await self.trigger_c1(opportunity)
        if isinstance(triggered, CycleResult):
            return triggered
        return await self.resolve_c2_hold(
            triggered,
            current_block=triggered.c1_inclusion_block,
        )

    async def trigger_c1(self, opportunity: Opportunity) -> PendingC2Hold | CycleResult:
        """Execute C1 and create a C2 hold after confirmed C1 inclusion.

        Sequence
        --------
        1.  Validate discovery fingerprint (spread > 0, prices > 0).
        2.  Assert execution math is viable (net_profit > min_profit_usd).
        3.  Build C1 payload (C1 envelope only — never touches C2 executor).
        4.  Fork-sim C1 — reject if sim fails.
        5.  Execute C1.
        6.  Wait for on-chain receipt (block number required for state reload).
        7.  Create a C2 hold bound to the same pair and lane.
        8.  Return immediately so later scanner cycles may continue.
        """
        cycle_id = self.cycle_logger.new_cycle_id(opportunity)

        # ── Gate 1: Discovery fingerprint ──────────────────────────────
        try:
            self._validate_discovery_fingerprint(opportunity)
        except ValueError as exc:
            return self._reject(cycle_id, opportunity, f"INVALID_FINGERPRINT: {exc}")

        # ── Gate 2: Execution math ─────────────────────────────────────
        try:
            opportunity.exec_math.assert_viable(opportunity.min_profit_usd)
        except ValueError as exc:
            return self._reject(cycle_id, opportunity, f"EXEC_MATH_NOT_VIABLE: {exc}")

        # ── C1: Build payload (C1 envelope — isolated) ─────────────────
        c1_payload = self.c1_executor.build_payload(opportunity)

        # ── Gate 3: Fork-sim before C1 send ────────────────────────────
        c1_sim = await self.fork_validator.validate_c1(
            cycle_id=cycle_id,
            opportunity=opportunity,
            payload=c1_payload,
        )
        if not c1_sim.ok:
            return self._reject(
                cycle_id, opportunity,
                f"C1_FORK_SIM_FAILED: {c1_sim.reason or 'sim rejected'}",
            )
        if not math.isfinite(c1_sim.net_profit_usd) or c1_sim.net_profit_usd <= opportunity.min_profit_usd:
            return self._reject(
                cycle_id, opportunity,
                "C1_NET_PROFIT_REJECTED",
            )

        # ── C1: Execute ────────────────────────────────────────────────
        c1_tx_hash = await self.c1_executor.execute(c1_payload)
        if not c1_tx_hash:
            return self._reject(cycle_id, opportunity, "C1_EMPTY_TX_HASH")

        # ── C1: Wait for on-chain confirmation ─────────────────────────
        c1_receipt = await self.c1_executor.wait_for_flash_arb_executed(
            tx_hash=c1_tx_hash
        )
        if c1_receipt is None or getattr(c1_receipt, "block_number", None) is None:
            return self._reject(cycle_id, opportunity, "C1_RECEIPT_MISSING")
        if getattr(c1_receipt, "status", 1) in (0, False):
            return self._reject(cycle_id, opportunity, "C1_RECEIPT_FAILED")
        c1_inclusion_block = int(c1_receipt.block_number)
        pair_id = self._pair_id(opportunity)
        hold = PendingC2Hold(
            cycle_id=cycle_id,
            opportunity=opportunity,
            c1_tx_hash=c1_tx_hash,
            c1_inclusion_block=c1_inclusion_block,
            expires_at_block=c1_inclusion_block + self.C2_MAX_BLOCK_LIFETIME,
            pair_id=pair_id,
            lane_id=opportunity.lane_id,
        )
        self.pending_c2_holds[cycle_id] = hold
        return hold

    async def resolve_c2_hold(
        self,
        hold: PendingC2Hold,
        *,
        current_block: Optional[int] = None,
    ) -> CycleResult:
        """Re-evaluate post-C1 state and decide MIRROR, REVERSE, or DO_NOTHING."""
        opportunity = hold.opportunity
        if self._is_expired(hold, current_block):
            return self._complete_c2_hold(
                hold,
                self._finalize(
                    cycle_id=hold.cycle_id,
                    opportunity=opportunity,
                    c1_tx_hash=hold.c1_tx_hash,
                    c2_tx_hash=None,
                    decision=C2Decision.DO_NOTHING,
                    status="C2_HOLD_EXPIRED",
                    hold=hold,
                ),
            )

        # ── State reload — POST-C1 state, min_block enforced ──────────
        post_c1_state = await self.state_mirror.reload_target_pools(
            pools=opportunity.target_pools,
            min_block=hold.c1_inclusion_block,
        )
        if self._is_expired(hold, self._state_block_number(post_c1_state, current_block)):
            return self._complete_c2_hold(
                hold,
                self._finalize(
                    cycle_id=hold.cycle_id,
                    opportunity=opportunity,
                    c1_tx_hash=hold.c1_tx_hash,
                    c2_tx_hash=None,
                    decision=C2Decision.DO_NOTHING,
                    status="C2_HOLD_EXPIRED",
                    hold=hold,
                ),
            )

        # ── C2: Recompute candidates from post-C1 state ────────────────
        candidates = self.c2_decision_engine.build_candidates(
            original_opportunity=opportunity,
            post_c1_state=post_c1_state,
        )

        decision = self.c2_decision_engine.select_best_candidate(candidates)
        if not self._matches_hold_affinity(hold, decision):
            return self._complete_c2_hold(
                hold,
                self._finalize(
                    cycle_id=hold.cycle_id,
                    opportunity=opportunity,
                    c1_tx_hash=hold.c1_tx_hash,
                    c2_tx_hash=None,
                    decision=C2Decision.DO_NOTHING,
                    status="C2_AFFINITY_REJECTED",
                    hold=hold,
                ),
            )
        decision = replace(decision, pair_id=hold.pair_id, lane_id=hold.lane_id)

        # ── C2: DO_NOTHING short-circuit ──────────────────────────────
        if decision.kind == C2Decision.DO_NOTHING:
            return self._complete_c2_hold(hold, self._finalize(
                cycle_id=hold.cycle_id,
                opportunity=opportunity,
                c1_tx_hash=hold.c1_tx_hash,
                c2_tx_hash=None,
                decision=C2Decision.DO_NOTHING,
                status="C2_DO_NOTHING",
                hold=hold,
            ))

        # ── C2: Net-profit pre-check before Merkle ────────────────────
        if not decision.exec_math.is_viable(decision.min_profit_usd):
            return self._complete_c2_hold(hold, self._finalize(
                cycle_id=hold.cycle_id,
                opportunity=opportunity,
                c1_tx_hash=hold.c1_tx_hash,
                c2_tx_hash=None,
                decision=C2Decision.DO_NOTHING,
                status="C2_EXEC_MATH_NOT_VIABLE",
                hold=hold,
            ))

        # ── C2: Merkle root + proof ────────────────────────────────────
        root, proof = self.merkle_service.build_root_and_proof(
            params=decision.params,
            all_candidates=[c.params for c in candidates],
        )
        await self.c2_executor.update_merkle_root(root)

        # ── C2: Build payload (C2 envelope — isolated) ─────────────────
        c2_payload = self.c2_executor.build_payload(
            candidate=decision,
            proof=proof,
        )

        # ── Gate 4: Fork-sim before C2 send (post-C1 state) ───────────
        c2_sim = await self.fork_validator.validate_c2(
            cycle_id=hold.cycle_id,
            post_c1_state=post_c1_state,
            payload=c2_payload,
            decision=decision,
        )
        if not c2_sim.ok:
            return self._complete_c2_hold(hold, self._finalize(
                cycle_id=hold.cycle_id,
                opportunity=opportunity,
                c1_tx_hash=hold.c1_tx_hash,
                c2_tx_hash=None,
                decision=C2Decision.DO_NOTHING,
                status="C2_FORK_SIM_FAILED",
                hold=hold,
            ))

        # ── Gate 5: Net-profit gate on fork-sim result ─────────────────
        if c2_sim.net_profit_usd <= decision.min_profit_usd:
            return self._complete_c2_hold(hold, self._finalize(
                cycle_id=hold.cycle_id,
                opportunity=opportunity,
                c1_tx_hash=hold.c1_tx_hash,
                c2_tx_hash=None,
                decision=C2Decision.DO_NOTHING,
                status="C2_NET_PROFIT_REJECTED",
                hold=hold,
            ))

        # ── C2: Execute ────────────────────────────────────────────────
        c2_tx_hash = await self.c2_executor.execute(c2_payload)

        return self._complete_c2_hold(hold, self._finalize(
            cycle_id=hold.cycle_id,
            opportunity=opportunity,
            c1_tx_hash=hold.c1_tx_hash,
            c2_tx_hash=c2_tx_hash,
            decision=decision.kind,
            status="CYCLE_COMPLETE",
            hold=hold,
        ))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_discovery_fingerprint(self, opportunity: Opportunity) -> None:
        fp = opportunity.fingerprint
        if fp.leg1_buy_price <= 0:
            raise ValueError("fingerprint.leg1_buy_price must be > 0")
        if fp.leg2_sell_price <= 0:
            raise ValueError("fingerprint.leg2_sell_price must be > 0")
        if fp.raw_spread_bps <= 0:
            raise ValueError("fingerprint.raw_spread_bps must be > 0")

    @staticmethod
    def _pair_id(opportunity: Opportunity) -> str:
        if opportunity.pair_id:
            return opportunity.pair_id
        return f"{opportunity.fingerprint.leg1_pool}:{opportunity.fingerprint.leg2_pool}"

    @staticmethod
    def _state_block_number(post_c1_state: Any, fallback: Optional[int]) -> Optional[int]:
        if isinstance(post_c1_state, dict):
            value = post_c1_state.get("block_number")
        else:
            value = getattr(post_c1_state, "block_number", None)
        return int(value) if value is not None else fallback

    @staticmethod
    def _is_expired(hold: PendingC2Hold, current_block: Optional[int]) -> bool:
        return current_block is not None and int(current_block) > hold.expires_at_block

    @staticmethod
    def _matches_hold_affinity(hold: PendingC2Hold, decision: C2Candidate) -> bool:
        if decision.pair_id and decision.pair_id != hold.pair_id:
            return False
        if decision.lane_id is not None and decision.lane_id != hold.lane_id:
            return False
        return True

    def _complete_c2_hold(self, hold: PendingC2Hold, result: CycleResult) -> CycleResult:
        self.pending_c2_holds.pop(hold.cycle_id, None)
        if self.lane_manager is not None and hold.lane_id is not None:
            self.lane_manager.release_lane(hold.lane_id)
        return result

    def _reject(self, cycle_id: str, opportunity: Opportunity, reason: str) -> CycleResult:
        self.cycle_logger.reject(cycle_id, opportunity, reason)
        return CycleResult(
            cycle_id=cycle_id,
            c1_tx_hash=None,
            c2_tx_hash=None,
            c2_decision=C2Decision.DO_NOTHING,
            gross_profit_usd=0.0,
            net_profit_usd=0.0,
            raw_spread_bps=opportunity.fingerprint.raw_spread_bps,
            status=reason,
        )

    def _finalize(
        self,
        *,
        cycle_id: str,
        opportunity: Opportunity,
        c1_tx_hash: Optional[str],
        c2_tx_hash: Optional[str],
        decision: C2Decision,
        status: str,
        hold: Optional[PendingC2Hold] = None,
    ) -> CycleResult:
        result = CycleResult(
            cycle_id=cycle_id,
            c1_tx_hash=c1_tx_hash,
            c2_tx_hash=c2_tx_hash,
            c2_decision=decision,
            gross_profit_usd=opportunity.gross_profit_usd,
            net_profit_usd=opportunity.net_profit_usd if c2_tx_hash else 0.0,
            raw_spread_bps=opportunity.fingerprint.raw_spread_bps,
            status=status,
            pair_id=hold.pair_id if hold else self._pair_id(opportunity),
            lane_id=hold.lane_id if hold else opportunity.lane_id,
            c1_inclusion_block=hold.c1_inclusion_block if hold else None,
            c2_expires_at_block=hold.expires_at_block if hold else None,
        )
        self.cycle_logger.finalize(result)
        return result


# ---------------------------------------------------------------------------
# SSN (Session Sequence Number) generator — default CycleLogger helper
# ---------------------------------------------------------------------------

class DefaultCycleLogger:
    """Simple in-process cycle logger backed by a list.

    Suitable for tests and local dry-runs.  Replace with a persistent
    structured logger for production.
    """

    def __init__(self) -> None:
        self._log: List[Any] = []

    def new_cycle_id(self, opportunity: Opportunity) -> str:  # noqa: ARG002
        return f"cycle_{uuid.uuid4().hex[:12]}"

    def reject(self, cycle_id: str, opportunity: Opportunity, reason: str) -> None:
        self._log.append(
            {
                "event": "CYCLE_REJECTED",
                "cycle_id": cycle_id,
                "reason": reason,
                "raw_spread_bps": opportunity.fingerprint.raw_spread_bps,
            }
        )

    def finalize(self, result: CycleResult) -> None:
        self._log.append(
            {
                "event": "CYCLE_FINALIZED",
                "cycle_id": result.cycle_id,
                "status": result.status,
                "c2_decision": result.c2_decision,
                "net_profit_usd": result.net_profit_usd,
                "pair_id": result.pair_id,
                "lane_id": result.lane_id,
                "c1_inclusion_block": result.c1_inclusion_block,
                "c2_expires_at_block": result.c2_expires_at_block,
            }
        )

    @property
    def entries(self) -> List[Any]:
        return list(self._log)
