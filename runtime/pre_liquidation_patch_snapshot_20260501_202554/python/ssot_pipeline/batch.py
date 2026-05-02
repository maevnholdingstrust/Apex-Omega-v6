"""Batch simulator for the Dual Punch SSOT pipeline.

Runs N independent execution cycles for a fixed pool state and accumulates
statistics across the C1→C2→execution pipeline.
"""
from __future__ import annotations

from .degradation import ExecutionDegradationSimulator
from .math_core import two_leg_arb_profit
from .types import BatchSummary


def _profitability_gate(p_net: float, p_fill: float) -> bool:
    """Execute only when P_net × P(fill) > 0."""
    return p_net > 0.0 and p_fill > 0.0


class BatchSimulator:
    """Stress-test the full C1→C2→execution pipeline over N probabilistic runs.

    Each run independently:
      1. Computes the deterministic C1 result (same pool state every run).
      2. Derives EV = owner_submission_edge × p_fill and decides C2 action.
      3. If C2 strikes, samples a degradation factor and realizes actual profit.
      4. Accumulates statistics.

    The same pool state is used for every run.  This is intentional: the batch
    tests execution variability under fixed market conditions, not market drift
    (which requires updating reserves between cycles from new on-chain state).

    Parameters
    ----------
    degradation_simulator:
        :class:`ExecutionDegradationSimulator` instance used for each run.
    """

    def __init__(self, degradation_simulator: ExecutionDegradationSimulator) -> None:
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
            Owner submission gas in asset A.
        p_fill:
            Fill probability; drives the C2 EV gate for every run.
        n_runs:
            Number of independent cycles to simulate.

        Returns
        -------
        BatchSummary
        """
        math = two_leg_arb_profit(
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
        owner_submission_edge = math.get("owner_submission_edge", p_net_det - c_total)
        ev = owner_submission_edge * p_fill

        c2_decision = "STRIKE" if _profitability_gate(owner_submission_edge, p_fill) else "DO_NOTHING"

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
