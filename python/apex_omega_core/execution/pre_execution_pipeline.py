from typing import Any, Tuple

from apex_omega_core.safety.execution_gates import gate_candidate, gate_executable_candidate
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

    executable_gate = gate_executable_candidate(candidate, fork_result)
    if not executable_gate.accepted:
        return False, executable_gate.reason, executable_gate

    return True, "READY_FOR_EXECUTION", fork_result
