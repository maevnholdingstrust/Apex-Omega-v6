"""Data containers for the Dual Punch SSOT pipeline (Phase 1).

These dataclasses are shared by all modules within the ssot_pipeline package
and serve as the single source of truth for pipeline output types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class RouteAuditResult:
    """Result of a single route envelope audit pass.

    Attributes
    ----------
    passed:
        ``True`` iff all canonical invariants were satisfied.
    violations:
        Human-readable descriptions of every invariant that failed.  Empty
        when ``passed`` is ``True``.
    """

    passed: bool
    violations: List[str] = field(default_factory=list)


@dataclass
class ExecutionRunResult:
    """Result of one simulated execution run.

    Attributes
    ----------
    a_in:
        Trade input amount (asset A) chosen by C1 sizing.
    b_out_1:
        Swap 1 output (asset B); identical to ``b_in_2`` by the locked
        inventory identity.
    a_out_2:
        Swap 2 output (asset A); final inventory.
    p_gross_deterministic:
        Gross profit computed deterministically by C1.
    p_net_deterministic:
        Net profit computed deterministically by C1 (after subtracting costs).
    p_net_actual:
        Realized profit after execution degradation.  Zero when C2 chose
        ``DO_NOTHING``.
    c2_decision:
        ``"STRIKE"`` or ``"DO_NOTHING"``.
    audit:
        Result of the route envelope audit for this run.
    """

    a_in: float
    b_out_1: float
    a_out_2: float
    p_gross_deterministic: float
    p_net_deterministic: float
    p_net_actual: float
    c2_decision: str
    audit: RouteAuditResult


@dataclass
class BatchSummary:
    """Aggregated results from a batch simulation.

    Attributes
    ----------
    n_runs:
        Total number of simulated cycles.
    n_strikes:
        Number of cycles where C2 decided ``STRIKE``.
    n_profitable_strikes:
        Number of strikes that yielded positive actual profit.
    total_actual_profit:
        Sum of ``p_net_actual`` across all runs.
    mean_actual_profit_per_run:
        ``total_actual_profit / n_runs`` (includes zero-profit DO_NOTHING runs).
    hit_rate:
        ``n_profitable_strikes / n_strikes``.  ``0.0`` when ``n_strikes == 0``.
    ev:
        Expected value per cycle = ``p_net_deterministic * p_fill``, computed
        from the first run's deterministic profit and the p_fill supplied to
        the simulator.
    """

    n_runs: int
    n_strikes: int
    n_profitable_strikes: int
    total_actual_profit: float
    mean_actual_profit_per_run: float
    hit_rate: float
    ev: float


@dataclass
class PipelineFinalResult:
    """Complete output of the SSOT pipeline finalizer.

    Attributes
    ----------
    best_size:
        The input amount (asset A) that yielded the highest net profit.
    p_net_deterministic:
        Net profit at the best size, before execution degradation.
    ev:
        Expected value = ``p_net_deterministic * p_fill``.
    c2_decision:
        ``"STRIKE"`` or ``"DO_NOTHING"`` based on the EV gate.
    audit:
        Route envelope audit result for the best-size plan.
    batch_summary:
        Aggregated statistics from the batch simulation.
    """

    best_size: float
    p_net_deterministic: float
    ev: float
    c2_decision: str
    audit: RouteAuditResult
    batch_summary: BatchSummary
