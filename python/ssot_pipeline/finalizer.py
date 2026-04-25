"""SSOT pipeline finalizer for the Dual Punch Phase 1 pipeline.

Top-level entrypoint that ties together best-size selection, payload audit,
C2 decision, and batch simulation into a single non-drifting reference
implementation for the locked 2-swap constant-product A→B→A cycle.
"""
from __future__ import annotations

import random
from typing import List, Optional

from .audit import audit_two_leg_route_envelope
from .batch import BatchSimulator
from .degradation import ExecutionDegradationSimulator
from .math_core import two_leg_arb_profit
from .types import PipelineFinalResult


def _profitability_gate(p_net: float, p_fill: float) -> bool:
    """Execute only when P_net × P(fill) > 0."""
    return p_net > 0.0 and p_fill > 0.0


class SSOTPipelineFinalizer:
    """Top-level entrypoint: run the full 2-leg SSOT pipeline and verify outputs.

    The finalizer implements the complete pipeline for the locked 2-swap
    constant-product model:

      1. **Best-size selection** — evaluate every candidate in ``sizes_to_test``
         via :func:`two_leg_arb_profit` and select the size with the highest
         net profit.  The optimum is interior (not always the largest size)
         because slippage curvature makes the profit function concave in trade
         size.

      2. **Payload audit** — run :func:`audit_two_leg_route_envelope` on the
         best-size plan.  The audit checks all canonical 2-swap invariants
         (inventory handoff, profit formulae, fee ranges) so that the execution
         envelope cannot silently drift from the math.

      3. **C2 decision** — apply the profitability gate (EV = p_net × p_fill > 0)
         to decide ``STRIKE`` or ``DO_NOTHING``.

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
        self._deg_sim = ExecutionDegradationSimulator(
            degradation_mean=degradation_mean,
            degradation_std=degradation_std,
            rng=rng,
        )
        self._batch_sim = BatchSimulator(self._deg_sim)

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
            If no valid candidate size is provided.
        """
        # ── Step 1: best-size selection ──────────────────────────────────────
        best_size: Optional[float] = None
        best_p_net = float("-inf")
        best_math: Optional[dict] = None

        for size in self.sizes_to_test:
            math = two_leg_arb_profit(
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
        c2_decision = "STRIKE" if _profitability_gate(best_p_net, self.p_fill) else "DO_NOTHING"

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
