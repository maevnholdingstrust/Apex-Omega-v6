"""Execution degradation simulator for the Dual Punch SSOT pipeline.

Models post-C1 execution variability without altering the locked inventory
identity (B_in_2 = B_out_1).
"""
from __future__ import annotations

import random
from typing import Optional

from .audit import audit_two_leg_route_envelope
from .types import ExecutionRunResult


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
        passing ``b_out_1`` as ``b_in_2`` to the audit.

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
            Owner submission gas component in asset A.
        p_fill:
            Fill probability used to gate the C2 decision; not sampled here.
        c2_decision:
            ``"STRIKE"`` or ``"DO_NOTHING"`` as decided upstream.
        fee1:
            Swap 1 DEX fee rate used in the original C1 computation (decimal).
        fee2:
            Swap 2 DEX fee rate used in the original C1 computation (decimal).

        Returns
        -------
        ExecutionRunResult
        """
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
