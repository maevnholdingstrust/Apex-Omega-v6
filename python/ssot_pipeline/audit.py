"""Route envelope auditor for the Dual Punch SSOT pipeline.

Verifies that a planned 2-leg execution envelope satisfies all canonical
constant-product invariants before the payload reaches the executor boundary.
"""
from __future__ import annotations

from typing import List

from .types import RouteAuditResult


def audit_two_leg_route_envelope(
    a_in: float,
    fee1: float,
    b_out_1: float,
    b_in_2: float,
    fee2: float,
    a_out_2: float,
    p_gross: float,
    p_net: float,
    c_total: float,
    tolerance: float = 1e-9,
) -> RouteAuditResult:
    """Audit a planned 2-leg route envelope against canonical constant-product invariants.

    Invariants checked
    ------------------
    1. ``B_in_2 == B_out_1`` — inventory handoff with no slippage subtraction
       between the two swaps.
    2. ``P_gross == A_out_2 − A_in`` — profit is measured after returning to the
       starting asset.
    3. ``P_net == P_gross − C_total`` — net profit accounts for all costs.
    4. ``fee1 ∈ [0, 1)`` and ``fee2 ∈ [0, 1)`` — fee rates are in valid range.

    Parameters
    ----------
    a_in:
        Starting amount of asset A.
    fee1:
        DEX fee rate for Swap 1 (decimal, e.g. 0.003 for 0.3%).
    b_out_1:
        Swap 1 output (asset B); the authoritative value used in the math.
    b_in_2:
        Swap 2 input as declared in the execution envelope; must equal
        ``b_out_1``.
    fee2:
        DEX fee rate for Swap 2 (decimal).
    a_out_2:
        Swap 2 output (asset A).
    p_gross:
        Declared gross profit in asset A.
    p_net:
        Declared net profit in asset A.
    c_total:
        Total declared cost in asset A (gas + flash-loan + other).
    tolerance:
        Absolute floating-point tolerance for equality checks.  Defaults to
        ``1e-9``, which is tight enough to catch semantic drift while tolerating
        IEEE-754 rounding at double precision.

    Returns
    -------
    RouteAuditResult
        ``passed=True`` when all four invariants hold; ``passed=False`` with a
        populated ``violations`` list otherwise.
    """
    violations: List[str] = []

    # 1. Inventory handoff: Swap 2 input must equal Swap 1 output exactly.
    if abs(b_in_2 - b_out_1) > tolerance:
        violations.append(
            f"inventory_drift: b_in_2={b_in_2:.10f} != b_out_1={b_out_1:.10f} "
            f"(delta={b_in_2 - b_out_1:.2e})"
        )

    # 2. Gross profit identity: P_gross == A_out_2 − A_in.
    expected_p_gross = a_out_2 - a_in
    if abs(p_gross - expected_p_gross) > tolerance:
        violations.append(
            f"p_gross_mismatch: declared={p_gross:.10f}, "
            f"expected A_out_2 - A_in={expected_p_gross:.10f} "
            f"(delta={p_gross - expected_p_gross:.2e})"
        )

    # 3. Net profit identity: P_net == P_gross − C_total.
    expected_p_net = p_gross - c_total
    if abs(p_net - expected_p_net) > tolerance:
        violations.append(
            f"p_net_mismatch: declared={p_net:.10f}, "
            f"expected P_gross - C_total={expected_p_net:.10f} "
            f"(delta={p_net - expected_p_net:.2e})"
        )

    # 4. Fee range checks: both fees must be in [0, 1).
    if fee1 < 0.0 or fee1 >= 1.0:
        violations.append(
            f"fee1_range: fee1={fee1} is outside [0, 1)"
        )
    if fee2 < 0.0 or fee2 >= 1.0:
        violations.append(
            f"fee2_range: fee2={fee2} is outside [0, 1)"
        )

    return RouteAuditResult(passed=len(violations) == 0, violations=violations)
