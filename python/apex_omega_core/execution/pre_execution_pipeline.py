from typing import Any, Tuple

from apex_omega_core.safety.execution_gates import gate_candidate
from apex_omega_core.execution.fork_validator import validate_on_fork


def pre_execution_pipeline(candidate: Any, c1_fn, c2_fn) -> Tuple[bool, str, Any]:
    """
    DISCOVERY
      â†“
    HARD GATES
      â†“
    C1
      â†“
    C2
      â†“
    FORK VALIDATION
      â†“
    EXECUTION
    """

    gate = gate_candidate(candidate)
    if not gate.accepted:
        return False, gate.reason, gate

    c1_result = c1_fn(candidate)
    c2_result = c2_fn(c1_result)

    decision = getattr(c2_result, "decision", None)
    if isinstance(c2_result, dict):
        decision = c2_result.get("decision")

    if decision not in {"STRIKE", True}:
        return False, "C2_DO_NOTHING", c2_result

    fork_ok, fork_result = validate_on_fork(c2_result)

    if not fork_ok:
        return False, fork_result.reason, fork_result

    return True, "READY_FOR_EXECUTION", fork_result

# ---------------------------------------------------------------------------
# Apex-Omega canonical decision symbols
# ---------------------------------------------------------------------------
# These are exported for tests and downstream dry-run/DNA logging modules.
# C2 output is intentionally restricted to EXECUTE / NO_OP.
C1_BUILD_PAYLOAD = "BUILD_PAYLOAD"
C1_REJECT = "REJECT"

C2_EXECUTE = "EXECUTE"
C2_NO_OP = "NO_OP"

C2_ALLOWED_DECISIONS = frozenset({C2_EXECUTE, C2_NO_OP})

CANONICAL_EXECUTION_FLOW = (
    "scanner",
    "gates",
    "C1",
    "fork_sim_c1",
    "execute_or_shadow_c1",
    "reload_or_shadow_mutate_state",
    "C2",
    "fork_sim_c2",
    "execute_or_no_op_c2",
    "logs_dashboard",
)

# ---------------------------------------------------------------------------
# Apex-Omega canonical public pipeline API
# ---------------------------------------------------------------------------
# Compatibility/public entrypoint required by canon-flow tests and dry-run DNA
# orchestration.  This function enforces the locked order:
#
# scanner/gates -> C1 -> fork sim -> execute/shadow C1 -> reload state
# -> C2 -> fork sim -> execute/no-op
#
# C2 is never called before C1 fork simulation + C1 execution/shadow execution
# + post-C1 state reload/shadow mutation.

if "C1_BUILD_PAYLOAD" not in globals():
    C1_BUILD_PAYLOAD = "BUILD_PAYLOAD"
if "C1_REJECT" not in globals():
    C1_REJECT = "REJECT"
if "C2_EXECUTE" not in globals():
    C2_EXECUTE = "EXECUTE"
if "C2_NO_OP" not in globals():
    C2_NO_OP = "NO_OP"

C2_ALLOWED_DECISIONS = frozenset({C2_EXECUTE, C2_NO_OP})

CANONICAL_EXECUTION_FLOW = (
    "scanner",
    "gates",
    "C1",
    "fork_sim_c1",
    "execute_or_shadow_c1",
    "reload_or_shadow_mutate_state",
    "C2",
    "fork_sim_c2",
    "execute_or_no_op_c2",
    "logs_dashboard",
)


def _apex_pick_callable(kwargs, *names):
    """Return the first callable supplied under one of *names*."""
    for name in names:
        value = kwargs.get(name)
        if callable(value):
            return value
    return None


def _apex_call(fn, *args, **kwargs):
    """Call a user-supplied stage function without forcing one rigid signature."""
    if fn is None:
        return args[0] if args else None

    attempts = (
        lambda: fn(*args, **kwargs),
        lambda: fn(*args),
        lambda: fn(**kwargs),
        lambda: fn(),
    )

    last_error = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc

    raise last_error


def canonical_execution_pipeline(candidate=None, **kwargs):
    """Run the locked Apex-Omega C1state-reloadC2 canonical flow.

    This public function intentionally keeps C1 and C2 separate:

    - C1 builds/simulates the first strike from pre-C1 state.
    - C1 execution is mechanical/shadow-only in dry-run.
    - State is reloaded or shadow-mutated after C1.
    - C2 evaluates only the post-C1 state.
    - C2 returns EXECUTE or NO_OP only.

    The function accepts flexible stage callables so tests and adapters can pass
    whichever local names they use.

    Supported callable aliases:
      gate/gates/gate_fn
      c1/c1_engine/c1_fn/aggressor
      fork_sim_c1/fork1/fork_simulator/fork_sim
      execute_c1/shadow_execute_c1/executor_c1
      reload_state/state_reloader/post_c1_reload/shadow_mutate_state
      c2/c2_engine/c2_fn/surgeon
      fork_sim_c2/fork2
      execute_c2/shadow_execute_c2/executor_c2
      logger/log_fn
    """

    events = []

    def mark(name):
        events.append(name)
        return name

    gate_fn = _apex_pick_callable(kwargs, "gate", "gates", "gate_fn")
    c1_fn = _apex_pick_callable(kwargs, "c1", "c1_engine", "c1_fn", "aggressor")
    fork1_fn = _apex_pick_callable(kwargs, "fork_sim_c1", "fork1", "fork_simulator", "fork_sim")
    execute1_fn = _apex_pick_callable(kwargs, "execute_c1", "shadow_execute_c1", "executor_c1")
    reload_fn = _apex_pick_callable(
        kwargs,
        "reload_state",
        "state_reloader",
        "post_c1_reload",
        "shadow_mutate_state",
    )
    c2_fn = _apex_pick_callable(kwargs, "c2", "c2_engine", "c2_fn", "surgeon")
    fork2_fn = _apex_pick_callable(kwargs, "fork_sim_c2", "fork2")
    execute2_fn = _apex_pick_callable(kwargs, "execute_c2", "shadow_execute_c2", "executor_c2")
    log_fn = _apex_pick_callable(kwargs, "logger", "log_fn")

    mark("scanner")
    working_candidate = candidate if candidate is not None else kwargs.get("scanner_candidate")

    mark("gates")
    gated = _apex_call(gate_fn, working_candidate) if gate_fn else working_candidate

    if gated is False:
        result = {
            "status": "REJECT",
            "c1_decision": C1_REJECT,
            "c2_decision": None,
            "events": events,
            "canonical_flow": CANONICAL_EXECUTION_FLOW,
            "reason": "gate_rejected",
        }
        _apex_call(log_fn, result) if log_fn else None
        return result

    mark("C1")
    c1_result = _apex_call(c1_fn, gated) if c1_fn else gated

    mark("fork_sim_c1")
    c1_fork_result = _apex_call(fork1_fn, c1_result) if fork1_fn else c1_result

    mark("execute_or_shadow_c1")
    c1_execution_result = (
        _apex_call(execute1_fn, c1_fork_result)
        if execute1_fn
        else c1_fork_result
    )

    mark("reload_or_shadow_mutate_state")
    post_c1_state = (
        _apex_call(reload_fn, c1_execution_result)
        if reload_fn
        else c1_execution_result
    )

    mark("C2")
    c2_result = _apex_call(c2_fn, post_c1_state) if c2_fn else {"decision": C2_NO_OP}

    if isinstance(c2_result, str):
        c2_decision = c2_result
    elif isinstance(c2_result, dict):
        c2_decision = c2_result.get("decision", c2_result.get("c2_decision", C2_NO_OP))
    else:
        c2_decision = getattr(c2_result, "decision", C2_NO_OP)

    if c2_decision == "DO_NOTHING":
        c2_decision = C2_NO_OP

    if c2_decision not in C2_ALLOWED_DECISIONS:
        raise ValueError(
            f"C2 decision must be one of {sorted(C2_ALLOWED_DECISIONS)}, got {c2_decision!r}"
        )

    c2_fork_result = None
    c2_execution_result = None

    if c2_decision == C2_EXECUTE:
        mark("fork_sim_c2")
        c2_fork_result = _apex_call(fork2_fn, c2_result) if fork2_fn else c2_result

        mark("execute_or_no_op_c2")
        c2_execution_result = (
            _apex_call(execute2_fn, c2_fork_result)
            if execute2_fn
            else c2_fork_result
        )
    else:
        mark("execute_or_no_op_c2")

    mark("logs_dashboard")

    result = {
        "status": "OK",
        "c1_decision": C1_BUILD_PAYLOAD,
        "c2_decision": c2_decision,
        "candidate": working_candidate,
        "gated": gated,
        "c1_result": c1_result,
        "c1_fork_result": c1_fork_result,
        "c1_execution_result": c1_execution_result,
        "post_c1_state": post_c1_state,
        "c2_result": c2_result,
        "c2_fork_result": c2_fork_result,
        "c2_execution_result": c2_execution_result,
        "events": events,
        "canonical_flow": CANONICAL_EXECUTION_FLOW,
        "c2_never_pre_approved_c1": True,
    }

    _apex_call(log_fn, result) if log_fn else None
    return result

# ---------------------------------------------------------------------------
# Apex-Omega canonical flow API override
# ---------------------------------------------------------------------------
# Test-compatible public entrypoint.
# Signature required by python/apex_omega_core/tests/test_canon_flow_pipeline.py:
#
# canonical_execution_pipeline(
#     candidate,
#     c1_fn,
#     c2_fn,
#     execute_c1_fn,
#     reload_state_fn,
#     *,
#     execute_c2_fn=None,
#     fork_validate_fn=None,
# )
#
# Locked order:
#   C1 -> fork sim C1 -> execute/shadow C1 -> reload/shadow state
#   -> C2 -> fork sim C2 only if C2 EXECUTE -> execute C2 or NO_OP

if "C2_EXECUTE" not in globals():
    C2_EXECUTE = "EXECUTE"

if "C2_NO_OP" not in globals():
    C2_NO_OP = "NO_OP"

if "C1_BUILD_PAYLOAD" not in globals():
    C1_BUILD_PAYLOAD = "BUILD_PAYLOAD"

if "C1_REJECT" not in globals():
    C1_REJECT = "REJECT"

C2_ALLOWED_DECISIONS = frozenset({C2_EXECUTE, C2_NO_OP})


class CanonicalPipelineResult(dict):
    """Dict result with attribute access for tests/adapters."""

    def __getattr__(self, name):
        if name == "accepted":
            if "accepted" in self:
                return bool(self["accepted"])
            return self.get("status") == "OK"
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _canon_action(obj, default=None):
    """Extract action/decision from object, dict, namespace, or string."""
    if obj is None:
        return default

    if isinstance(obj, str):
        return obj

    if isinstance(obj, dict):
        return obj.get("action", obj.get("decision", obj.get("c2_decision", default)))

    return getattr(obj, "action", getattr(obj, "decision", default))


def _canon_stage(obj, fallback="unknown"):
    if obj is None:
        return fallback

    if isinstance(obj, dict):
        return obj.get("stage", fallback)

    return getattr(obj, "stage", fallback)


def _canon_fork_validate(fork_validate_fn, trade):
    """Return (passed: bool, fork_result)."""
    if fork_validate_fn is None:
        return True, CanonicalPipelineResult(
            passed=True,
            stage=_canon_stage(trade),
            reason=None,
        )

    result = fork_validate_fn(trade)

    if isinstance(result, tuple) and len(result) == 2:
        passed, fork_result = result
        return bool(passed), fork_result

    passed = bool(result)
    return passed, result


def canonical_execution_pipeline(
    candidate,
    c1_fn,
    c2_fn,
    execute_c1_fn,
    reload_state_fn,
    *,
    execute_c2_fn=None,
    fork_validate_fn=None,
    log_fn=None,
):
    """Run the locked Apex-Omega C1 -> post-C1 reload -> C2 flow.

    Canon:
    - C2 is never called before C1 fork validation.
    - C2 is never called before C1 execution/shadow execution.
    - C2 is never called before post-C1 reload/shadow mutation.
    - C2 receives post-C1 state only.
    - C2 output is restricted to EXECUTE / NO_OP.
    - C1 fork sim and C2 fork sim are separate validations.
    """

    events = []

    def mark(event):
        events.append(event)
        return event

    # ------------------------------------------------------------------
    # C1: Aggressor computes/builds first strike.
    # ------------------------------------------------------------------
    mark("C1")
    c1_result = c1_fn(candidate)

    # ------------------------------------------------------------------
    # C1 fork/static simulation must pass before C1 execution.
    # ------------------------------------------------------------------
    mark("FORK_SIM_C1")
    c1_fork_passed, c1_fork_result = _canon_fork_validate(
        fork_validate_fn,
        c1_result,
    )

    if not c1_fork_passed:
        result = CanonicalPipelineResult(
            status="C1_FORK_SIM_FAILED",
            terminal_state="C1_FORK_SIM_FAILED",
            c1_decision=C1_REJECT,
            c2_decision=None,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=False,
            c1_fork_result=c1_fork_result,
            c1_execution_result=None,
            post_c1_state=None,
            c2_result=None,
            c2_fork_passed=None,
            c2_fork_result=None,
            c2_execution_result=None,
            events=events,
            c2_never_pre_approved_c1=True,
            c2_called=False,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C1 execution/shadow execution.
    # ------------------------------------------------------------------
    mark("EXECUTE_OR_SHADOW_C1")
    c1_execution_result = execute_c1_fn(c1_result, c1_fork_result)

    # ------------------------------------------------------------------
    # Mandatory state boundary before C2.
    # ------------------------------------------------------------------
    mark("RELOAD_OR_SHADOW_MUTATE_STATE")
    post_c1_state = reload_state_fn(candidate, c1_result, c1_execution_result)

    # ------------------------------------------------------------------
    # C2: Surgeon evaluates post-C1 state only.
    # ------------------------------------------------------------------
    mark("C2")
    c2_result = c2_fn(post_c1_state)
    c2_decision = _canon_action(c2_result, default=C2_NO_OP)

    # Backward alias guard, but canonical output remains NO_OP.
    if c2_decision == "DO_NOTHING":
        c2_decision = C2_NO_OP

    # Reject non-canonical C2 output without running C2 fork or execution.
    if c2_decision not in C2_ALLOWED_DECISIONS:
        result = CanonicalPipelineResult(
            status="C2_NON_CANONICAL_ACTION",
            terminal_state="C2_NON_CANONICAL_ACTION",
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=c2_decision,
            allowed_c2_decisions=sorted(C2_ALLOWED_DECISIONS),
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=None,
            c2_fork_result=None,
            c2_execution_result=None,
            events=events,
            c2_never_pre_approved_c1=True,
            c2_called=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C2 NO_OP: valid terminal state. No C2 execution.
    # ------------------------------------------------------------------
    if c2_decision == C2_NO_OP:
        mark("C2_NO_OP")
        result = CanonicalPipelineResult(
            status="OK",
            terminal_state="C2_NO_OP",
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=C2_NO_OP,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=None,
            c2_fork_result=None,
            c2_execution_result=None,
            events=events,
            c2_never_pre_approved_c1=True,
            c2_called=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C2 EXECUTE: separate fork/static simulation.
    # ------------------------------------------------------------------
    mark("FORK_SIM_C2")
    c2_fork_passed, c2_fork_result = _canon_fork_validate(
        fork_validate_fn,
        c2_result,
    )

    if not c2_fork_passed:
        result = CanonicalPipelineResult(
            status="C2_FORK_SIM_FAILED",
            terminal_state="C2_FORK_SIM_FAILED",
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=C2_EXECUTE,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=False,
            c2_fork_result=c2_fork_result,
            c2_execution_result=None,
            events=events,
            c2_never_pre_approved_c1=True,
            c2_called=True,
        )
        if log_fn:
            log_fn(result)
        return result

    mark("EXECUTE_OR_SHADOW_C2")
    if execute_c2_fn is not None:
        c2_execution_result = execute_c2_fn(c2_result, c2_fork_result)
    else:
        c2_execution_result = c2_fork_result

    result = CanonicalPipelineResult(
        status="OK",
        terminal_state="C2_EXECUTE",
        c1_decision=C1_BUILD_PAYLOAD,
        c2_decision=C2_EXECUTE,
        candidate=candidate,
        c1_result=c1_result,
        c1_fork_passed=True,
        c1_fork_result=c1_fork_result,
        c1_execution_result=c1_execution_result,
        post_c1_state=post_c1_state,
        c2_result=c2_result,
        c2_fork_passed=True,
        c2_fork_result=c2_fork_result,
        c2_execution_result=c2_execution_result,
        events=events,
        c2_never_pre_approved_c1=True,
        c2_called=True,
    )
    if log_fn:
        log_fn(result)
    return result


__all__ = [
    "C1_BUILD_PAYLOAD",
    "C1_REJECT",
    "C2_EXECUTE",
    "C2_NO_OP",
    "C2_ALLOWED_DECISIONS",
    "CanonicalPipelineResult",
    "canonical_execution_pipeline",
]


# ---------------------------------------------------------------------------
# FINAL Apex-Omega canon-flow override
# ---------------------------------------------------------------------------
# This final override matches test_canon_flow_pipeline.py exactly:
#
# Required sequence:
#   C1
#   -> fork_validate(C1)
#   -> execute C1
#   -> reload/shadow mutate state
#   -> C2
#   -> fork_validate(C2)
#   -> EXECUTE or NO_OP
#
# C2 is never called before C1 fork simulation, C1 execution, and post-C1
# state reload/shadow mutation. C2 fork validation is separate and always
# happens after C2 returns a canonical action, including NO_OP.

if "C2_EXECUTE" not in globals():
    C2_EXECUTE = "EXECUTE"

if "C2_NO_OP" not in globals():
    C2_NO_OP = "NO_OP"

if "C1_BUILD_PAYLOAD" not in globals():
    C1_BUILD_PAYLOAD = "BUILD_PAYLOAD"

if "C1_REJECT" not in globals():
    C1_REJECT = "REJECT"

C2_ALLOWED_DECISIONS = frozenset({C2_EXECUTE, C2_NO_OP})


class CanonicalPipelineResult(dict):
    """Dictionary result with attribute access expected by tests."""

    def __getattr__(self, name):
        if name == "accepted":
            return bool(self.get("accepted", self.get("status") == "OK"))
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _canon_get_action(obj, default=None):
    if obj is None:
        return default
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("action", obj.get("decision", obj.get("c2_decision", default)))
    return getattr(obj, "action", getattr(obj, "decision", default))


def _canon_get_reason(obj, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get("reason", default)
    return getattr(obj, "reason", default)


def _canon_fork_validate(fork_validate_fn, trade):
    if fork_validate_fn is None:
        return True, CanonicalPipelineResult(
            stage=getattr(trade, "stage", "unknown"),
            passed=True,
            reason=None,
        )

    result = fork_validate_fn(trade)

    if isinstance(result, tuple) and len(result) == 2:
        passed, fork_result = result
        return bool(passed), fork_result

    return bool(result), result


def canonical_execution_pipeline(
    candidate,
    c1_fn,
    c2_fn,
    execute_c1_fn,
    reload_state_fn,
    *,
    execute_c2_fn=None,
    fork_validate_fn=None,
    log_fn=None,
):
    """Execute the locked C1 -> post-C1 reload -> C2 canon flow.

    Test contract:
    - C2 must not be called before C1 fork sim.
    - C2 must not be called before C1 execution and reload.
    - C2 receives post-C1 state.
    - C2 may return EXECUTE or NO_OP only.
    - C2 fork validation is separate from C1 fork validation.
    - C2 fork validation happens for both EXECUTE and NO_OP.
    """

    internal_events = []

    def mark(name):
        internal_events.append(name)

    # ------------------------------------------------------------------
    # C1 AGGRESSOR
    # ------------------------------------------------------------------
    mark("C1")
    c1_result = c1_fn(candidate)

    # ------------------------------------------------------------------
    # C1 FORK SIM  if this fails, C2 must never be called.
    # ------------------------------------------------------------------
    mark("FORK_SIM_C1")
    c1_fork_passed, c1_fork_result = _canon_fork_validate(
        fork_validate_fn,
        c1_result,
    )

    if not c1_fork_passed:
        reason = _canon_get_reason(c1_fork_result, "C1_FORK_SIM_FAILED")
        result = CanonicalPipelineResult(
            accepted=False,
            status="C1_FORK_SIM_FAILED",
            terminal_state="C1_FORK_SIM_FAILED",
            reason=reason,
            c1_decision=C1_REJECT,
            c2_decision=None,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=False,
            c1_fork_result=c1_fork_result,
            c1_execution_result=None,
            post_c1_state=None,
            c2_result=None,
            c2_fork_passed=None,
            c2_fork_result=None,
            c2_execution_result=None,
            events=internal_events,
            c2_called=False,
            c2_never_pre_approved_c1=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C1 EXECUTION / SHADOW EXECUTION
    # ------------------------------------------------------------------
    mark("EXECUTE_OR_SHADOW_C1")
    c1_execution_result = execute_c1_fn(c1_result, c1_fork_result)

    # ------------------------------------------------------------------
    # POST-C1 STATE BOUNDARY
    # ------------------------------------------------------------------
    mark("RELOAD_OR_SHADOW_MUTATE_STATE")
    post_c1_state = reload_state_fn(candidate, c1_result, c1_execution_result)

    # ------------------------------------------------------------------
    # C2 SURGEON  receives post-C1 state only.
    # ------------------------------------------------------------------
    mark("C2")
    c2_result = c2_fn(post_c1_state)
    c2_decision = _canon_get_action(c2_result, C2_NO_OP)

    if c2_decision == "DO_NOTHING":
        c2_decision = C2_NO_OP

    # Invalid C2 action must reject before C2 fork/execute.
    if c2_decision not in C2_ALLOWED_DECISIONS:
        result = CanonicalPipelineResult(
            accepted=False,
            status="C2_NON_CANONICAL_ACTION",
            terminal_state="C2_NON_CANONICAL_ACTION",
            reason="INVALID_C2_ACTION",
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=c2_decision,
            allowed_c2_decisions=sorted(C2_ALLOWED_DECISIONS),
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=None,
            c2_fork_result=None,
            c2_execution_result=None,
            events=internal_events,
            c2_called=True,
            c2_never_pre_approved_c1=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C2 FORK SIM  always separate, even for NO_OP.
    # This matches the test expectation that NO_OP still records fork:c2.
    # ------------------------------------------------------------------
    mark("FORK_SIM_C2")
    c2_fork_passed, c2_fork_result = _canon_fork_validate(
        fork_validate_fn,
        c2_result,
    )

    if not c2_fork_passed:
        reason = _canon_get_reason(c2_fork_result, "C2_FORK_SIM_FAILED")
        result = CanonicalPipelineResult(
            accepted=False,
            status="C2_FORK_SIM_FAILED",
            terminal_state="C2_FORK_SIM_FAILED",
            reason=reason,
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=c2_decision,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=False,
            c2_fork_result=c2_fork_result,
            c2_execution_result=None,
            events=internal_events,
            c2_called=True,
            c2_never_pre_approved_c1=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C2 NO_OP  valid accepted cycle; no C2 execution.
    # ------------------------------------------------------------------
    if c2_decision == C2_NO_OP:
        mark("C2_NO_OP")
        result = CanonicalPipelineResult(
            accepted=True,
            status="OK",
            terminal_state="C2_NO_OP",
            reason=C2_NO_OP,
            c1_decision=C1_BUILD_PAYLOAD,
            c2_decision=C2_NO_OP,
            candidate=candidate,
            c1_result=c1_result,
            c1_fork_passed=True,
            c1_fork_result=c1_fork_result,
            c1_execution_result=c1_execution_result,
            post_c1_state=post_c1_state,
            c2_result=c2_result,
            c2_fork_passed=True,
            c2_fork_result=c2_fork_result,
            c2_execution_result=None,
            events=internal_events,
            c2_called=True,
            c2_never_pre_approved_c1=True,
        )
        if log_fn:
            log_fn(result)
        return result

    # ------------------------------------------------------------------
    # C2 EXECUTE  valid accepted cycle; execute/shadow execute C2.
    # ------------------------------------------------------------------
    mark("EXECUTE_OR_SHADOW_C2")
    if execute_c2_fn is not None:
        c2_execution_result = execute_c2_fn(c2_result, c2_fork_result)
    else:
        c2_execution_result = c2_fork_result

    result = CanonicalPipelineResult(
        accepted=True,
        status="OK",
        terminal_state="C2_EXECUTE",
        reason=C2_EXECUTE,
        c1_decision=C1_BUILD_PAYLOAD,
        c2_decision=C2_EXECUTE,
        candidate=candidate,
        c1_result=c1_result,
        c1_fork_passed=True,
        c1_fork_result=c1_fork_result,
        c1_execution_result=c1_execution_result,
        post_c1_state=post_c1_state,
        c2_result=c2_result,
        c2_fork_passed=True,
        c2_fork_result=c2_fork_result,
        c2_execution_result=c2_execution_result,
        events=internal_events,
        c2_called=True,
        c2_never_pre_approved_c1=True,
    )
    if log_fn:
        log_fn(result)
    return result


__all__ = [
    "C1_BUILD_PAYLOAD",
    "C1_REJECT",
    "C2_EXECUTE",
    "C2_NO_OP",
    "C2_ALLOWED_DECISIONS",
    "CanonicalPipelineResult",
    "canonical_execution_pipeline",
]

# ---------------------------------------------------------------------------
# Compatibility patch: expose result.c2_action as alias of result.c2_decision
# ---------------------------------------------------------------------------
try:
    _previous_canonical_result_getattr_for_c2_action = CanonicalPipelineResult.__getattr__

    def _canonical_result_getattr_with_c2_action(self, name):
        if name == "c2_action":
            if "c2_action" in self:
                return self["c2_action"]
            return self.get("c2_decision")
        return _previous_canonical_result_getattr_for_c2_action(self, name)

    CanonicalPipelineResult.__getattr__ = _canonical_result_getattr_with_c2_action
except NameError:
    pass
