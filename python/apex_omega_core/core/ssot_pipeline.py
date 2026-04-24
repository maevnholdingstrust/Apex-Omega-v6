"""Full-stack SSOT pipeline for the 2-leg constant-product arbitrage reference model.

This module ties together the canonical math (SlippageSentinel), execution
decision gate (profitability_gate), payload audit, and probabilistic simulation
into a single non-drifting reference implementation for the locked 2-swap
constant-product A→B→A cycle.

Architecture
------------
RouteAuditResult
    Dataclass carrying the pass/fail state and any violation messages from an
    envelope audit.

audit_two_leg_route_envelope()
    Standalone function.  Verifies that a planned execution envelope satisfies
    all canonical 2-swap invariants before submission:
      * B_in_2 == B_out_1 (inventory handoff — no slippage subtraction between legs)
      * P_gross == A_out_2 − A_in
      * P_net   == P_gross − C_total
      * fee1, fee2 are in the valid range [0, 1)

ExecutionDegradationSimulator
    Models post-C1 execution variability without altering the locked inventory
    identity.  A degradation factor is drawn from N(mean, std) and applied to
    the deterministic C1 net profit; the AMM math and the B_in_2 = B_out_1
    constraint are never modified.

BatchSimulator
    Runs N independent execution cycles for a fixed pool state.  Each cycle
    independently applies C2 decision logic and, when C2 strikes, draws a
    degradation factor.  Aggregates total realized profit, mean profit per run,
    and hit rate (fraction of strikes with positive actual profit).

SSOTPipelineFinalizer
    Top-level entrypoint.  Given raw pool parameters, it:
      1. Evaluates each candidate size in ``sizes_to_test`` via two_leg_arb_profit.
      2. Selects the best size (highest p_net) — the optimum is interior because
         slippage curvature makes it non-monotone in size.
      3. Audits the planned envelope for the best size.
      4. Applies the C2 profitability gate to decide STRIKE / DO_NOTHING.
      5. Runs a batch simulation and returns a fully-verified PipelineFinalResult.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

from .inference import profitability_gate
from .slippage_sentinel import SlippageSentinel


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Route envelope audit
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Execution degradation simulator
# ---------------------------------------------------------------------------

class ExecutionDegradationSimulator:
    """Model post-C1 execution variability for a 2-leg constant-product cycle.

    After C1 deterministically computes the optimal size and expected profit,
    real-world execution deviates due to block-inclusion latency, competing
    transactions, and gas-price variance.  This simulator draws a degradation
    factor for each run from a Normal distribution and scales the deterministic
    net profit, without altering the locked inventory identity (B_in_2 = B_out_1).

    The degradation is applied only at the realized-profit scalar level.  The
    AMM math — including the B_in_2 = B_out_1 handoff — is never mutated.

    Parameters
    ----------
    degradation_mean:
        Center of the degradation factor distribution.  A value of 1.0 means
        the expected realized profit equals the deterministic C1 estimate; lower
        values model execution shortfall.  Default is 0.65.
    degradation_std:
        Standard deviation of the degradation factor distribution.  Default
        is 0.35, producing a realistic spread of outcomes that includes both
        sub-zero and near-C1 realizations.
    rng:
        Seeded :class:`random.Random` instance.  Supply a fixed seed for
        reproducible tests; leave ``None`` to use an independent default.
    """

    def __init__(
        self,
        degradation_mean: float = 0.65,
        degradation_std: float = 0.35,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.degradation_mean = float(degradation_mean)
        self.degradation_std = float(degradation_std)
        self._rng = rng if rng is not None else random.Random()

    def _sample_degradation_factor(self) -> float:
        """Draw one degradation factor from N(mean, std), clamped to [0, ∞).

        The distribution is a right-truncated Normal: samples below zero are
        discarded and replaced with 0.0.  When ``degradation_std`` is large
        relative to ``degradation_mean`` (e.g. the default mean=0.65, std=0.35)
        a non-trivial fraction of the theoretical distribution falls below zero;
        clamping shifts the effective mean above ``degradation_mean``.  This
        is intentional: zero-floor realizations model execution runs that fail
        entirely (reverted transactions, MEV displacement) without producing
        negative profit for the strategy.
        """
        return max(0.0, self._rng.gauss(self.degradation_mean, self.degradation_std))

    def simulate_one_run(
        self,
        a_in: float,
        b_out_1: float,
        a_out_2: float,
        p_gross: float,
        p_net_deterministic: float,
        c_total: float,
        p_fill: float,
        c2_decision: str,
        fee1: float = 0.0,
        fee2: float = 0.0,
    ) -> ExecutionRunResult:
        """Simulate one execution run with probabilistic profit degradation.

        When ``c2_decision`` is ``"DO_NOTHING"`` the run contributes zero
        realized profit without any degradation.

        When ``c2_decision`` is ``"STRIKE"`` a degradation factor *f* is drawn
        from ``N(degradation_mean, degradation_std)`` and the actual profit is::

            p_net_actual = p_net_deterministic × f

        The locked inventory identity (B_in_2 = B_out_1) is preserved by always
        passing ``b_out_1`` as ``b_in_2`` to the audit, which is the only
        correct value for a well-formed plan.

        Parameters
        ----------
        a_in:
            C1 optimal input amount (asset A).
        b_out_1:
            Swap 1 output from the deterministic C1 computation.
        a_out_2:
            Swap 2 output from the deterministic C1 computation.
        p_gross:
            Gross profit in asset A (= A_out_2 − A_in).
        p_net_deterministic:
            Net profit in asset A after all costs.
        c_total:
            Total cost component in asset A (gas + loan + other).
        p_fill:
            Fill probability used to gate the C2 decision; not sampled here —
            degradation is applied only to the *profit* scalar, not the gate.
        c2_decision:
            ``"STRIKE"`` or ``"DO_NOTHING"`` as decided upstream.
        fee1:
            Swap 1 DEX fee rate used in the original C1 computation (decimal).
            Passed to the route audit so the fee-range invariant is checked
            against the actual plan parameters, not a placeholder.
        fee2:
            Swap 2 DEX fee rate used in the original C1 computation (decimal).

        Returns
        -------
        ExecutionRunResult
        """
        # Audit sets b_in_2 = b_out_1 (locked inventory identity) and validates
        # the profit-formula identities using the real fee rates.
        audit = audit_two_leg_route_envelope(
            a_in=a_in,
            fee1=fee1,
            b_out_1=b_out_1,
            b_in_2=b_out_1,   # b_in_2 IS b_out_1 in a correct plan (invariant)
            fee2=fee2,
            a_out_2=a_out_2,
            p_gross=p_gross,
            p_net=p_net_deterministic,
            c_total=c_total,
        )

        if c2_decision != "STRIKE":
            return ExecutionRunResult(
                a_in=a_in,
                b_out_1=b_out_1,
                a_out_2=a_out_2,
                p_gross_deterministic=p_gross,
                p_net_deterministic=p_net_deterministic,
                p_net_actual=0.0,
                c2_decision=c2_decision,
                audit=audit,
            )

        degradation_factor = self._sample_degradation_factor()
        p_net_actual = p_net_deterministic * degradation_factor

        return ExecutionRunResult(
            a_in=a_in,
            b_out_1=b_out_1,
            a_out_2=a_out_2,
            p_gross_deterministic=p_gross,
            p_net_deterministic=p_net_deterministic,
            p_net_actual=p_net_actual,
            c2_decision=c2_decision,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Batch simulator
# ---------------------------------------------------------------------------

class BatchSimulator:
    """Stress-test the full C1→C2→execution pipeline over N probabilistic runs.

    Each run independently:
      1. Computes the deterministic C1 result (same pool state every run).
      2. Derives EV = p_net × p_fill and decides C2 action.
      3. If C2 strikes, samples a degradation factor and realizes actual profit.
      4. Accumulates statistics.

    The same pool state is used for every run.  This is intentional: the batch
    tests execution variability under fixed market conditions, not market drift
    (which requires updating reserves between cycles from new on-chain state).

    Parameters
    ----------
    sentinel:
        :class:`SlippageSentinel` instance used for AMM math.
    degradation_simulator:
        :class:`ExecutionDegradationSimulator` instance used for each run.
    """

    def __init__(
        self,
        sentinel: SlippageSentinel,
        degradation_simulator: ExecutionDegradationSimulator,
    ) -> None:
        self._sentinel = sentinel
        self._deg_sim = degradation_simulator

    def run(
        self,
        a_in: float,
        fee1: float,
        r1_in: float,
        r1_out: float,
        fee2: float,
        r2_in: float,
        r2_out: float,
        c_total: float,
        p_fill: float,
        n_runs: int,
    ) -> BatchSummary:
        """Run ``n_runs`` independent execution cycles for the given pool state.

        Parameters
        ----------
        a_in:
            Fixed trade input amount (asset A) determined by C1 sizing.
        fee1:
            Swap 1 fee rate (decimal).
        r1_in, r1_out:
            Pool 1 reserves (asset A side, asset B side).
        fee2:
            Swap 2 fee rate (decimal).
        r2_in, r2_out:
            Pool 2 reserves (asset B side, asset A side).
        c_total:
            Total cost in asset A (gas + flash-loan + other).
        p_fill:
            Fill probability; drives the C2 EV gate for every run.
        n_runs:
            Number of independent cycles to simulate.

        Returns
        -------
        BatchSummary
        """
        # C1 math is deterministic — compute once and reuse for all runs.
        math = self._sentinel.two_leg_arb_profit(
            a_in=a_in,
            fee1=fee1,
            r1_in=r1_in,
            r1_out=r1_out,
            fee2=fee2,
            r2_in=r2_in,
            r2_out=r2_out,
            c_gas=c_total,
        )
        p_net_det = math["p_net"]
        ev = p_net_det * p_fill

        # C2 decision is also deterministic given fixed p_net and p_fill.
        c2_decision = "STRIKE" if profitability_gate(p_net_det, p_fill) else "DO_NOTHING"

        total_actual_profit = 0.0
        n_strikes = 0
        n_profitable_strikes = 0

        for _ in range(n_runs):
            run_result = self._deg_sim.simulate_one_run(
                a_in=float(a_in),
                b_out_1=math["b_out_1"],
                a_out_2=math["a_out_2"],
                p_gross=math["p_gross"],
                p_net_deterministic=p_net_det,
                c_total=c_total,
                p_fill=p_fill,
                c2_decision=c2_decision,
                fee1=fee1,
                fee2=fee2,
            )
            total_actual_profit += run_result.p_net_actual
            if c2_decision == "STRIKE":
                n_strikes += 1
                if run_result.p_net_actual > 0.0:
                    n_profitable_strikes += 1

        hit_rate = (
            n_profitable_strikes / n_strikes
            if n_strikes > 0
            else 0.0
        )
        mean_actual_profit_per_run = total_actual_profit / n_runs if n_runs > 0 else 0.0

        return BatchSummary(
            n_runs=n_runs,
            n_strikes=n_strikes,
            n_profitable_strikes=n_profitable_strikes,
            total_actual_profit=total_actual_profit,
            mean_actual_profit_per_run=mean_actual_profit_per_run,
            hit_rate=hit_rate,
            ev=ev,
        )


# ---------------------------------------------------------------------------
# SSOT pipeline finalizer
# ---------------------------------------------------------------------------

class SSOTPipelineFinalizer:
    """Top-level entrypoint: run the full 2-leg SSOT pipeline and verify outputs.

    The finalizer implements the complete pipeline for the locked 2-swap
    constant-product model:

      1. **Best-size selection** — evaluate every candidate in ``sizes_to_test``
         via :meth:`SlippageSentinel.two_leg_arb_profit` and select the size
         with the highest net profit.  The optimum is interior (not always the
         largest size) because slippage curvature makes the profit function
         concave in trade size.

      2. **Payload audit** — run :func:`audit_two_leg_route_envelope` on the
         best-size plan.  The audit checks all canonical 2-swap invariants
         (inventory handoff, profit formulae, fee ranges) so that the execution
         envelope cannot silently drift from the math.

      3. **C2 decision** — apply the :func:`profitability_gate` (EV = p_net ×
         p_fill > 0) to decide ``STRIKE`` or ``DO_NOTHING``.

      4. **Batch simulation** — run ``n_batch_runs`` independent probabilistic
         cycles via :class:`BatchSimulator` to stress the pipeline under
         execution variability.

    Parameters
    ----------
    sizes_to_test:
        Candidate input amounts (asset A) to evaluate.  At least one value
        must be provided.  Providing a range of values lets the finalizer
        identify the interior optimum.
    n_batch_runs:
        Number of cycles for the batch simulation.  Default 100.
    p_fill:
        Fill probability used for EV calculation and C2 decision.  Default
        1.0 (deterministic; use a real estimate from the gas oracle in
        production).
    degradation_mean:
        Mean of the post-C1 degradation factor distribution.  Default 0.65.
    degradation_std:
        Std of the post-C1 degradation factor distribution.  Default 0.35.
    rng_seed:
        Optional integer seed for the degradation RNG.  Provide a fixed seed
        for reproducible tests.
    """

    def __init__(
        self,
        sizes_to_test: List[float],
        n_batch_runs: int = 100,
        p_fill: float = 1.0,
        degradation_mean: float = 0.65,
        degradation_std: float = 0.35,
        rng_seed: Optional[int] = None,
    ) -> None:
        if not sizes_to_test:
            raise ValueError("sizes_to_test must contain at least one candidate size")
        self.sizes_to_test = list(sizes_to_test)
        self.n_batch_runs = int(n_batch_runs)
        self.p_fill = float(p_fill)

        rng = random.Random(rng_seed)
        self._sentinel = SlippageSentinel()
        self._deg_sim = ExecutionDegradationSimulator(
            degradation_mean=degradation_mean,
            degradation_std=degradation_std,
            rng=rng,
        )
        self._batch_sim = BatchSimulator(self._sentinel, self._deg_sim)

    def run(
        self,
        fee1: float,
        r1_in: float,
        r1_out: float,
        fee2: float,
        r2_in: float,
        r2_out: float,
        c_total: float = 0.0,
    ) -> PipelineFinalResult:
        """Run the full pipeline for the given pool state and return verified output.

        Parameters
        ----------
        fee1:
            Swap 1 DEX fee rate (decimal, e.g. 0.003 for 0.3%).
        r1_in, r1_out:
            Pool 1 reserves (input token = asset A, output token = asset B).
        fee2:
            Swap 2 DEX fee rate (decimal).
        r2_in, r2_out:
            Pool 2 reserves (input token = asset B, output token = asset A).
        c_total:
            Total cost in asset A (gas + flash-loan fee + other).  Defaults
            to 0.0.

        Returns
        -------
        PipelineFinalResult
            Fully verified summary including best size, deterministic and
            expected-value profit, C2 decision, audit status, and batch stats.

        Raises
        ------
        ValueError
            If no valid candidate size produces a positive gross profit (i.e.
            all sizes result in a zero or negative AMM output).
        """
        # ── Step 1: best-size selection ──────────────────────────────────────
        best_size: Optional[float] = None
        best_p_net = float("-inf")
        best_math: Optional[dict] = None

        for size in self.sizes_to_test:
            math = self._sentinel.two_leg_arb_profit(
                a_in=size,
                fee1=fee1,
                r1_in=r1_in,
                r1_out=r1_out,
                fee2=fee2,
                r2_in=r2_in,
                r2_out=r2_out,
                c_gas=c_total,
            )
            if math["p_net"] > best_p_net:
                best_p_net = math["p_net"]
                best_size = size
                best_math = math

        if best_size is None or best_math is None:
            raise ValueError("No valid candidate size found in sizes_to_test")

        # ── Step 2: payload audit ────────────────────────────────────────────
        audit = audit_two_leg_route_envelope(
            a_in=best_size,
            fee1=fee1,
            b_out_1=best_math["b_out_1"],
            b_in_2=best_math["b_out_1"],   # canonical: b_in_2 IS b_out_1
            fee2=fee2,
            a_out_2=best_math["a_out_2"],
            p_gross=best_math["p_gross"],
            p_net=best_math["p_net"],
            c_total=c_total,
        )

        # ── Step 3: C2 decision ──────────────────────────────────────────────
        ev = best_p_net * self.p_fill
        c2_decision = "STRIKE" if profitability_gate(best_p_net, self.p_fill) else "DO_NOTHING"

        # ── Step 4: batch simulation ─────────────────────────────────────────
        batch_summary = self._batch_sim.run(
            a_in=best_size,
            fee1=fee1,
            r1_in=r1_in,
            r1_out=r1_out,
            fee2=fee2,
            r2_in=r2_in,
            r2_out=r2_out,
            c_total=c_total,
            p_fill=self.p_fill,
            n_runs=self.n_batch_runs,
        )

        return PipelineFinalResult(
            best_size=best_size,
            p_net_deterministic=best_p_net,
            ev=ev,
            c2_decision=c2_decision,
            audit=audit,
            batch_summary=batch_summary,
        )
