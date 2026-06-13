"""Tests for the C1 → C2 Cycle Orchestrator.

Proves the following invariants:
1.  C1 executes before C2.
2.  C2 cannot use pre-C1 state.
3.  C2 recomputes from post-C1 state.
4.  C2 selects MIRROR when profitable.
5.  C2 selects REVERSE when profitable.
6.  C2 selects DO_NOTHING when no net edge.
7.  C2 rejects if net profit below threshold.
8.  Cycle logs exactly one SSN ID.
9.  C1 envelope is never sent to C2.
10. C2 envelope is never sent to C1.
11. Fork-sim is required before C1 send.
12. Fork-sim is required before C2 send.
"""
from __future__ import annotations

import pytest

from apex_omega_core.orchestrator.cycle_orchestrator import (
    C2Candidate,
    C2Decision,
    CycleOrchestrator,
    CycleResult,
    DefaultCycleLogger,
    ExecutionMath,
    ForkSimResult,
    Opportunity,
    OpportunityFingerprint,
    PendingC2Hold,
)


# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------

class _Receipt:
    block_number: int = 99_000_000


class _FakeC1Executor:
    """Records every build_payload and execute call."""

    def __init__(self, tx_hash: str = "0xC1TX") -> None:
        self._tx_hash = tx_hash
        self.build_calls: list = []
        self.execute_calls: list = []
        self._last_built_payload = None

    def build_payload(self, opportunity: Opportunity) -> dict:
        payload = {"envelope": "C1_ENVELOPE", "opportunity": opportunity}
        self.build_calls.append(payload)
        self._last_built_payload = payload
        return payload

    async def execute(self, payload: dict) -> str:
        assert payload.get("envelope") == "C1_ENVELOPE", "C1 executor received non-C1 payload"
        self.execute_calls.append(payload)
        return self._tx_hash

    async def wait_for_flash_arb_executed(self, *, tx_hash: str) -> _Receipt:
        return _Receipt()


class _FakeC2Executor:
    """Records every build_payload and execute call."""

    def __init__(self, tx_hash: str = "0xC2TX") -> None:
        self._tx_hash = tx_hash
        self.build_calls: list = []
        self.execute_calls: list = []
        self.merkle_roots: list = []

    def build_payload(self, *, candidate: C2Candidate, proof) -> dict:
        payload = {"envelope": "C2_ENVELOPE", "candidate": candidate, "proof": proof}
        self.build_calls.append(payload)
        return payload

    async def update_merkle_root(self, root: bytes) -> None:
        self.merkle_roots.append(root)

    async def execute(self, payload: dict) -> str:
        assert payload.get("envelope") == "C2_ENVELOPE", "C2 executor received non-C2 payload"
        self.execute_calls.append(payload)
        return self._tx_hash


class _FakeForkValidator:
    """Returns configurable results for C1 and C2 sim."""

    def __init__(
        self,
        c1_ok: bool = True,
        c1_net_profit: float = 5.0,
        c2_ok: bool = True,
        c2_net_profit: float = 10.0,
    ) -> None:
        self.c1_calls: list = []
        self.c2_calls: list = []
        self._c1_ok = c1_ok
        self._c1_net_profit = c1_net_profit
        self._c2_ok = c2_ok
        self._c2_net_profit = c2_net_profit

    async def validate_c1(self, *, cycle_id, opportunity, payload) -> ForkSimResult:
        self.c1_calls.append({"cycle_id": cycle_id, "payload": payload})
        return ForkSimResult(ok=self._c1_ok, net_profit_usd=self._c1_net_profit)

    async def validate_c2(self, *, cycle_id, post_c1_state, payload, decision) -> ForkSimResult:
        self.c2_calls.append(
            {"cycle_id": cycle_id, "post_c1_state": post_c1_state, "payload": payload}
        )
        return ForkSimResult(ok=self._c2_ok, net_profit_usd=self._c2_net_profit)


class _FakeStateMirror:
    """Tracks reload calls and returns a distinct post-C1 state object."""

    def __init__(self) -> None:
        self.reload_calls: list = []
        self._state = object()  # unique sentinel

    async def reload_target_pools(self, *, pools, min_block) -> object:
        self.reload_calls.append({"pools": pools, "min_block": min_block})
        return self._state

    @property
    def post_c1_state(self) -> object:
        return self._state


class _FakeC2DecisionEngine:
    """Returns a configurable decision with a given kind."""

    def __init__(
        self,
        kind: C2Decision = C2Decision.MIRROR,
        net_profit: float = 10.0,
        min_profit: float = 2.0,
        amount_in: float = 1000.0,
        pair_id: str = "",
        lane_id: int | None = None,
    ) -> None:
        self._kind = kind
        self._net_profit = net_profit
        self._min_profit = min_profit
        self._amount_in = amount_in
        self._pair_id = pair_id
        self._lane_id = lane_id
        self.build_calls: list = []
        self.select_calls: list = []
        self._post_c1_state_received = None

    def build_candidates(
        self, *, original_opportunity, post_c1_state
    ) -> list[C2Candidate]:
        self._post_c1_state_received = post_c1_state
        self.build_calls.append(post_c1_state)
        math = ExecutionMath(
            amount_in=self._amount_in,
            expected_out=1020.0,
            min_out=1010.0,
            flash_fee=0.9,
            gas_cost=1.0,
            dex_fees=1.5,
            net_profit=self._net_profit,
        )
        return [
            C2Candidate(
                kind=self._kind,
                params={"kind": self._kind.value},
                exec_math=math,
                min_profit_usd=self._min_profit,
                pair_id=self._pair_id,
                lane_id=self._lane_id,
            ),
            C2Candidate(kind=C2Decision.DO_NOTHING, params={}, exec_math=math, min_profit_usd=self._min_profit),
        ]

    def select_best_candidate(self, candidates: list[C2Candidate]) -> C2Candidate:
        self.select_calls.append(candidates)
        return candidates[0]


class _FakeMerkleService:
    def build_root_and_proof(self, *, params, all_candidates):
        return (b"\x00" * 32, {"proof": "fake_proof"})


def _make_opportunity(
    min_profit: float = 2.0,
    net_profit: float = 8.0,
) -> Opportunity:
    fp = OpportunityFingerprint(
        leg1_buy_price=1.00,
        leg2_sell_price=1.05,
        raw_spread_bps=50.0,
    )
    math = ExecutionMath(
        amount_in=10_000.0,
        expected_out=10_080.0,
        min_out=10_050.0,
        flash_fee=9.0,
        gas_cost=3.0,
        dex_fees=5.0,
        net_profit=net_profit,
    )
    return Opportunity(
        fingerprint=fp,
        exec_math=math,
        target_pools=["0xPool1", "0xPool2"],
        gross_profit_usd=12.0,
        net_profit_usd=net_profit,
        min_profit_usd=min_profit,
        pair_id="USDCe/WMATIC",
        lane_id=7,
    )


def _make_orchestrator(
    c1_executor=None,
    c2_executor=None,
    fork_validator=None,
    state_mirror=None,
    decision_engine=None,
    merkle_service=None,
    cycle_logger=None,
) -> tuple[CycleOrchestrator, dict]:
    c1 = c1_executor or _FakeC1Executor()
    c2 = c2_executor or _FakeC2Executor()
    fv = fork_validator or _FakeForkValidator()
    sm = state_mirror or _FakeStateMirror()
    de = decision_engine or _FakeC2DecisionEngine()
    ms = merkle_service or _FakeMerkleService()
    lg = cycle_logger or DefaultCycleLogger()
    orch = CycleOrchestrator(
        discovery_engine=None,
        state_mirror=sm,
        c1_executor=c1,
        c2_executor=c2,
        fork_validator=fv,
        c2_decision_engine=de,
        merkle_service=ms,
        cycle_logger=lg,
    )
    return orch, {"c1": c1, "c2": c2, "fv": fv, "sm": sm, "de": de, "lg": lg}


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestCycleOrchestratorC1ThenC2:
    """All 12 required proofs for the C1 → C2 cycle orchestrator."""

    @pytest.mark.asyncio
    async def test_c1_executes_before_c2(self):
        """C1 must produce a tx_hash before C2 builds or executes anything."""
        call_order = []
        c1 = _FakeC1Executor()
        c2 = _FakeC2Executor()

        # Wrap build_payload to record order
        _orig_c1_build = c1.build_payload
        def _c1_build(opp):
            call_order.append("c1_build")
            return _orig_c1_build(opp)
        c1.build_payload = _c1_build

        _orig_c2_build = c2.build_payload
        def _c2_build(**kwargs):
            call_order.append("c2_build")
            return _orig_c2_build(**kwargs)
        c2.build_payload = _c2_build

        orch, _ = _make_orchestrator(c1_executor=c1, c2_executor=c2)
        result = await orch.run_cycle(_make_opportunity())

        assert "c1_build" in call_order
        assert "c2_build" in call_order
        assert call_order.index("c1_build") < call_order.index("c2_build"), (
            "C1 must build/execute before C2"
        )
        assert result.c1_tx_hash == "0xC1TX"
        assert result.c2_tx_hash == "0xC2TX"

    @pytest.mark.asyncio
    async def test_c2_cannot_use_pre_c1_state(self):
        """C2 decision engine must receive the post-C1 reloaded state, not pre-C1."""
        sm = _FakeStateMirror()
        de = _FakeC2DecisionEngine()
        orch, _ = _make_orchestrator(state_mirror=sm, decision_engine=de)
        await orch.run_cycle(_make_opportunity())

        assert len(sm.reload_calls) >= 1, "StateMirror.reload_target_pools was never called"
        assert de._post_c1_state_received is sm.post_c1_state, (
            "C2 decision engine received pre-C1 state instead of post-C1 reloaded state"
        )

    @pytest.mark.asyncio
    async def test_c2_recomputes_from_post_c1_state(self):
        """C2 build_candidates must be called with the reloaded post-C1 state object."""
        sm = _FakeStateMirror()
        de = _FakeC2DecisionEngine()
        orch, _ = _make_orchestrator(state_mirror=sm, decision_engine=de)
        await orch.run_cycle(_make_opportunity())

        # The state passed to build_candidates must be the object returned by reload
        assert len(de.build_calls) == 1
        assert de.build_calls[0] is sm.post_c1_state

    @pytest.mark.asyncio
    async def test_c2_selects_mirror_when_profitable(self):
        """When MIRROR is profitable, C2 executes and result records MIRROR."""
        de = _FakeC2DecisionEngine(kind=C2Decision.MIRROR, net_profit=10.0)
        fv = _FakeForkValidator(c2_net_profit=10.0)
        orch, _ = _make_orchestrator(decision_engine=de, fork_validator=fv)
        result = await orch.run_cycle(_make_opportunity())

        assert result.c2_decision == C2Decision.MIRROR
        assert result.c2_tx_hash == "0xC2TX"
        assert result.status == "CYCLE_COMPLETE"

    @pytest.mark.asyncio
    async def test_c2_selects_reverse_when_profitable(self):
        """When REVERSE is profitable, C2 executes and result records REVERSE."""
        de = _FakeC2DecisionEngine(kind=C2Decision.REVERSE, net_profit=10.0)
        fv = _FakeForkValidator(c2_net_profit=10.0)
        orch, _ = _make_orchestrator(decision_engine=de, fork_validator=fv)
        result = await orch.run_cycle(_make_opportunity())

        assert result.c2_decision == C2Decision.REVERSE
        assert result.c2_tx_hash == "0xC2TX"
        assert result.status == "CYCLE_COMPLETE"

    @pytest.mark.asyncio
    async def test_c2_selects_do_nothing_when_no_net_edge(self):
        """When the decision engine returns DO_NOTHING, C2 does not execute."""
        de = _FakeC2DecisionEngine(kind=C2Decision.DO_NOTHING, net_profit=-1.0)
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(decision_engine=de, c2_executor=c2)
        result = await orch.run_cycle(_make_opportunity())

        assert result.c2_decision == C2Decision.DO_NOTHING
        assert result.c2_tx_hash is None
        assert len(c2.execute_calls) == 0
        assert result.status == "C2_DO_NOTHING"

    @pytest.mark.asyncio
    async def test_c2_rejects_if_net_profit_below_threshold(self):
        """When fork-sim net profit <= min_profit_usd, C2 does not execute."""
        # min_profit_usd=2.0, sim returns 1.5 — should reject
        de = _FakeC2DecisionEngine(kind=C2Decision.MIRROR, net_profit=10.0, min_profit=2.0)
        fv = _FakeForkValidator(c2_ok=True, c2_net_profit=1.5)  # below threshold
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(decision_engine=de, fork_validator=fv, c2_executor=c2)
        result = await orch.run_cycle(_make_opportunity(min_profit=2.0))

        assert result.c2_tx_hash is None
        assert result.status == "C2_NET_PROFIT_REJECTED"
        assert len(c2.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_cycle_logs_single_ssn_id(self):
        """Each run_cycle emits exactly one finalize log entry with a unique cycle_id."""
        lg = DefaultCycleLogger()
        orch, _ = _make_orchestrator(cycle_logger=lg)
        result = await orch.run_cycle(_make_opportunity())

        finalized = [e for e in lg.entries if e.get("event") == "CYCLE_FINALIZED"]
        assert len(finalized) == 1, f"Expected 1 CYCLE_FINALIZED event, got {len(finalized)}"
        assert finalized[0]["cycle_id"] == result.cycle_id
        assert result.cycle_id.startswith("cycle_")

    @pytest.mark.asyncio
    async def test_c1_envelope_never_sent_to_c2(self):
        """The payload object produced by c1_executor.build_payload must never
        reach c2_executor.build_payload or c2_executor.execute."""
        c1 = _FakeC1Executor()
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(c1_executor=c1, c2_executor=c2)
        await orch.run_cycle(_make_opportunity())

        assert len(c1.build_calls) == 1
        c1_payload = c1.build_calls[0]

        for c2_call in c2.build_calls:
            assert c2_call is not c1_payload, "C1 envelope leaked into C2 build_payload"
            assert c2_call.get("envelope") != "C1_ENVELOPE", "C1 envelope type reached C2"

        for c2_exec in c2.execute_calls:
            assert c2_exec is not c1_payload, "C1 envelope leaked into C2 execute"
            assert c2_exec.get("envelope") != "C1_ENVELOPE", "C1 envelope type executed by C2"

    @pytest.mark.asyncio
    async def test_c2_envelope_never_sent_to_c1(self):
        """The payload object produced by c2_executor.build_payload must never
        reach c1_executor.execute."""
        c1 = _FakeC1Executor()
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(c1_executor=c1, c2_executor=c2)
        await orch.run_cycle(_make_opportunity())

        assert len(c2.build_calls) == 1
        c2_payload = c2.build_calls[0]

        for c1_exec in c1.execute_calls:
            assert c1_exec is not c2_payload, "C2 envelope leaked into C1 execute"
            assert c1_exec.get("envelope") != "C2_ENVELOPE", "C2 envelope type executed by C1"

    @pytest.mark.asyncio
    async def test_fork_sim_required_before_c1_send(self):
        """fork_validator.validate_c1 must be called before c1_executor.execute."""
        call_order = []
        fv = _FakeForkValidator()
        c1 = _FakeC1Executor()

        _orig_validate = fv.validate_c1
        async def _tracked_validate_c1(**kwargs):
            call_order.append("fork_sim_c1")
            return await _orig_validate(**kwargs)
        fv.validate_c1 = _tracked_validate_c1

        _orig_execute = c1.execute
        async def _tracked_execute(payload):
            call_order.append("c1_execute")
            return await _orig_execute(payload)
        c1.execute = _tracked_execute

        orch, _ = _make_orchestrator(fork_validator=fv, c1_executor=c1)
        await orch.run_cycle(_make_opportunity())

        assert "fork_sim_c1" in call_order, "fork_validator.validate_c1 was never called"
        assert "c1_execute" in call_order, "c1_executor.execute was never called"
        assert call_order.index("fork_sim_c1") < call_order.index("c1_execute"), (
            "fork_sim_c1 must happen before c1_execute"
        )

    @pytest.mark.asyncio
    async def test_fork_sim_required_before_c2_send(self):
        """fork_validator.validate_c2 must be called before c2_executor.execute."""
        call_order = []
        fv = _FakeForkValidator()
        c2 = _FakeC2Executor()

        _orig_validate_c2 = fv.validate_c2
        async def _tracked_validate_c2(**kwargs):
            call_order.append("fork_sim_c2")
            return await _orig_validate_c2(**kwargs)
        fv.validate_c2 = _tracked_validate_c2

        _orig_c2_execute = c2.execute
        async def _tracked_c2_execute(payload):
            call_order.append("c2_execute")
            return await _orig_c2_execute(payload)
        c2.execute = _tracked_c2_execute

        orch, _ = _make_orchestrator(fork_validator=fv, c2_executor=c2)
        await orch.run_cycle(_make_opportunity())

        assert "fork_sim_c2" in call_order, "fork_validator.validate_c2 was never called"
        assert "c2_execute" in call_order, "c2_executor.execute was never called"
        assert call_order.index("fork_sim_c2") < call_order.index("c2_execute"), (
            "fork_sim_c2 must happen before c2_execute"
        )

    # ------------------------------------------------------------------
    # Regression: C1 fork-sim failure aborts the cycle cleanly
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_c1_fork_sim_failure_aborts_cycle(self):
        """If C1 fork-sim fails, neither C1 nor C2 executes."""
        fv = _FakeForkValidator(c1_ok=False)
        c1 = _FakeC1Executor()
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(fork_validator=fv, c1_executor=c1, c2_executor=c2)
        result = await orch.run_cycle(_make_opportunity())

        assert result.c1_tx_hash is None
        assert result.c2_tx_hash is None
        assert "C1_FORK_SIM_FAILED" in result.status
        assert len(c1.execute_calls) == 0
        assert len(c2.execute_calls) == 0

    # ------------------------------------------------------------------
    # Regression: C2 fork-sim failure falls back to DO_NOTHING (not crash)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_c2_fork_sim_failure_falls_back_to_do_nothing(self):
        """If C2 fork-sim fails, C1 tx is still recorded but C2 does not execute."""
        fv = _FakeForkValidator(c1_ok=True, c2_ok=False)
        c2 = _FakeC2Executor()
        orch, _ = _make_orchestrator(fork_validator=fv, c2_executor=c2)
        result = await orch.run_cycle(_make_opportunity())

        assert result.c1_tx_hash == "0xC1TX"
        assert result.c2_tx_hash is None
        assert result.status == "C2_FORK_SIM_FAILED"
        assert len(c2.execute_calls) == 0

    # ------------------------------------------------------------------
    # Regression: exec_math with negative net_profit is rejected before C1
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_negative_net_profit_rejects_before_c1(self):
        """Opportunities with net_profit <= min_profit_usd are rejected before C1 fires."""
        opp = _make_opportunity(min_profit=5.0, net_profit=-3.0)
        c1 = _FakeC1Executor()
        orch, _ = _make_orchestrator(c1_executor=c1)
        result = await orch.run_cycle(opp)

        assert result.c1_tx_hash is None
        assert "EXEC_MATH_NOT_VIABLE" in result.status
        assert len(c1.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_min_out_below_profitable_floor_rejects_before_c1(self):
        """The slippage floor must still repay all declared costs and min profit."""
        opp = _make_opportunity()
        opp.exec_math = ExecutionMath(
            amount_in=10_000.0,
            expected_out=10_080.0,
            min_out=10_010.0,
            flash_fee=9.0,
            gas_cost=3.0,
            dex_fees=5.0,
            net_profit=8.0,
        )
        c1 = _FakeC1Executor()
        orch, _ = _make_orchestrator(c1_executor=c1)
        result = await orch.run_cycle(opp)

        assert "EXEC_MATH_NOT_VIABLE" in result.status
        assert len(c1.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_c1_fork_sim_net_profit_must_clear_threshold(self):
        """A passing fork call is insufficient when simulated net profit degraded."""
        fv = _FakeForkValidator(c1_ok=True, c1_net_profit=1.5)
        c1 = _FakeC1Executor()
        orch, _ = _make_orchestrator(fork_validator=fv, c1_executor=c1)
        result = await orch.run_cycle(_make_opportunity(min_profit=2.0))

        assert result.status == "C1_NET_PROFIT_REJECTED"
        assert len(c1.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_c1_trigger_creates_non_blocking_c2_hold(self):
        """C1 confirmation creates a hold without evaluating or executing C2."""
        c2 = _FakeC2Executor()
        de = _FakeC2DecisionEngine()
        orch, _ = _make_orchestrator(c2_executor=c2, decision_engine=de)

        hold = await orch.trigger_c1(_make_opportunity())

        assert isinstance(hold, PendingC2Hold)
        assert hold.pair_id == "USDCe/WMATIC"
        assert hold.lane_id == 7
        assert hold.expires_at_block == hold.c1_inclusion_block + 5
        assert hold.cycle_id in orch.pending_c2_holds
        assert len(de.build_calls) == 0
        assert len(c2.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_multiple_c2_holds_do_not_block_later_c1_cycles(self):
        """Later C1 triggers are admitted while earlier C2 decisions remain pending."""
        orch, _ = _make_orchestrator()

        first = await orch.trigger_c1(_make_opportunity())
        second = await orch.trigger_c1(_make_opportunity())

        assert isinstance(first, PendingC2Hold)
        assert isinstance(second, PendingC2Hold)
        assert first.cycle_id != second.cycle_id
        assert set(orch.pending_c2_holds) == {first.cycle_id, second.cycle_id}

    @pytest.mark.asyncio
    async def test_c2_hold_expires_after_five_blocks(self):
        """C2 must choose DO_NOTHING when resolution starts after the hold window."""
        c2 = _FakeC2Executor()
        de = _FakeC2DecisionEngine()
        orch, _ = _make_orchestrator(c2_executor=c2, decision_engine=de)
        hold = await orch.trigger_c1(_make_opportunity())

        result = await orch.resolve_c2_hold(
            hold,
            current_block=hold.c1_inclusion_block + 6,
        )

        assert result.status == "C2_HOLD_EXPIRED"
        assert result.c2_decision == C2Decision.DO_NOTHING
        assert hold.cycle_id not in orch.pending_c2_holds
        assert len(de.build_calls) == 0
        assert len(c2.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_c2_rejects_pair_or_lane_drift(self):
        """C2 may not leave the triggering C1 pair or execution lane."""
        c2 = _FakeC2Executor()
        de = _FakeC2DecisionEngine(pair_id="USDCe/WETH", lane_id=8)
        orch, _ = _make_orchestrator(c2_executor=c2, decision_engine=de)
        hold = await orch.trigger_c1(_make_opportunity())

        result = await orch.resolve_c2_hold(hold)

        assert result.status == "C2_AFFINITY_REJECTED"
        assert result.c2_decision == C2Decision.DO_NOTHING
        assert len(c2.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_c2_recalculates_flashloan_size_with_same_math_gate(self):
        """C2 uses post-C1 ExecutionMath sizing and may select a new viable amount."""
        de = _FakeC2DecisionEngine(amount_in=500.0, net_profit=10.0)
        orch, ctx = _make_orchestrator(decision_engine=de)
        hold = await orch.trigger_c1(_make_opportunity())

        result = await orch.resolve_c2_hold(hold)

        assert result.status == "CYCLE_COMPLETE"
        candidate = ctx["c2"].build_calls[0]["candidate"]
        assert candidate.exec_math.amount_in == 500.0
        assert candidate.exec_math.amount_in != hold.opportunity.exec_math.amount_in
        assert candidate.pair_id == hold.pair_id
        assert candidate.lane_id == hold.lane_id
