from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from apex_omega_core.execution.fork_validator import validate_on_fork
from apex_omega_core.safety.execution_gates import gate_candidate, gate_executable_candidate


C2_EXECUTE = "EXECUTE"
C2_NO_OP = "NO_OP"
C2_ACTIONS = {C2_EXECUTE, C2_NO_OP}


@dataclass(frozen=True)
class CanonFlowResult:
    accepted: bool
    reason: str
    c1_result: Any = None
    c1_fork_result: Any = None
    c1_execution_result: Any = None
    post_c1_state: Any = None
    c2_result: Any = None
    c2_fork_result: Any = None
    c2_action: Optional[str] = None
    c2_execution_result: Any = None


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _c2_action(c2_result: Any) -> Optional[str]:
    action = _get(c2_result, "action", _get(c2_result, "decision", None))
    if action is None:
        return None
    normalized = str(action).upper()
    return normalized


def canonical_execution_pipeline(
    candidate: Any,
    c1_fn: Callable[[Any], Any],
    c2_fn: Callable[[Any], Any],
    execute_c1_fn: Callable[..., Any],
    reload_state_fn: Callable[..., Any],
    execute_c2_fn: Optional[Callable[[Any, Any], Any]] = None,
    fork_validate_fn: Callable[[Any], Tuple[bool, Any]] = validate_on_fork,
) -> CanonFlowResult:
    """
    Canonical execution order:

    gate -> C1 -> fork sim -> execute C1 -> reload state -> C2 -> fork sim
    -> execute/no-op.

    C2 is deliberately downstream of C1 execution and state reload. It never
    approves, blocks, or pre-filters C1.
    """

    gate = gate_candidate(candidate)
    if not gate.accepted:
        return CanonFlowResult(False, gate.reason or "GATE_REJECTED")

    c1_result = c1_fn(candidate)

    c1_fork_ok, c1_fork_result = fork_validate_fn(c1_result)
    if not c1_fork_ok:
        return CanonFlowResult(False, _get(c1_fork_result, "reason", "C1_FORK_FAILED"), c1_result, c1_fork_result)

    executable_gate = gate_executable_candidate(candidate, c1_fork_result)
    if not executable_gate.accepted:
        return CanonFlowResult(
            False,
            executable_gate.reason or "C1_EXECUTABLE_GATE_REJECTED",
            c1_result,
            c1_fork_result,
        )

    c1_execution_result = execute_c1_fn(c1_result, c1_fork_result)
    post_c1_state = reload_state_fn(candidate, c1_result, c1_execution_result)

    c2_result = c2_fn(post_c1_state)
    c2_action = _c2_action(c2_result)
    if c2_action not in C2_ACTIONS:
        return CanonFlowResult(
            False,
            "INVALID_C2_ACTION",
            c1_result,
            c1_fork_result,
            c1_execution_result,
            post_c1_state,
            c2_result,
            c2_action=c2_action,
        )

    c2_fork_ok, c2_fork_result = fork_validate_fn(c2_result)
    if not c2_fork_ok:
        return CanonFlowResult(
            False,
            _get(c2_fork_result, "reason", "C2_FORK_FAILED"),
            c1_result,
            c1_fork_result,
            c1_execution_result,
            post_c1_state,
            c2_result,
            c2_fork_result,
            c2_action,
        )

    if c2_action == C2_NO_OP:
        return CanonFlowResult(
            True,
            C2_NO_OP,
            c1_result,
            c1_fork_result,
            c1_execution_result,
            post_c1_state,
            c2_result,
            c2_fork_result,
            c2_action,
        )

    c2_execution_result = execute_c2_fn(c2_result, c2_fork_result) if execute_c2_fn else c2_fork_result
    return CanonFlowResult(
        True,
        C2_EXECUTE,
        c1_result,
        c1_fork_result,
        c1_execution_result,
        post_c1_state,
        c2_result,
        c2_fork_result,
        c2_action,
        c2_execution_result,
    )


def pre_execution_pipeline(
    candidate: Any,
    c1_fn: Callable[[Any], Any],
    c2_fn: Callable[[Any], Any],
    execute_c1_fn: Callable[..., Any],
    reload_state_fn: Callable[..., Any],
    execute_c2_fn: Optional[Callable[[Any, Any], Any]] = None,
    fork_validate_fn: Callable[[Any], Tuple[bool, Any]] = validate_on_fork,
) -> Tuple[bool, str, Any]:
    """Compatibility wrapper around the canonical C1/post-state/C2 flow."""

    result = canonical_execution_pipeline(
        candidate,
        c1_fn,
        c2_fn,
        execute_c1_fn,
        reload_state_fn,
        execute_c2_fn=execute_c2_fn,
        fork_validate_fn=fork_validate_fn,
    )
    return result.accepted, result.reason, result
