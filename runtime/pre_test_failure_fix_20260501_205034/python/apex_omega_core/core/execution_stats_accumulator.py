"""execution_stats_accumulator.py — Rolling Feed E: execution outcome tracker.

Records the result of every live :meth:`ContractInvoker.invoke` call and
exposes a rolling-window :class:`~apex_omega_core.core.domain_types.ExecutionStats`
snapshot so that the C1/C2 intake pipeline always has a calibrated view of
recent inclusion rates, revert rates, and realized slippage error.

Usage
-----
::

    acc = ExecutionStatsAccumulator(window_size=200)

    # After every invoke() call:
    acc.record(
        included=invocation["executed_onchain"],
        reverted=not invocation["success"] and invocation["executed_onchain"],
        slippage_error_bps=computed_slip_err,
        pnl_error_bps=computed_pnl_err,
        router=target_address,
    )

    stats: ExecutionStats = acc.get_stats()
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-execution outcome record
# ---------------------------------------------------------------------------

@dataclass
class ExecutionOutcome:
    """Outcome of a single contract invocation."""
    included: bool          # transaction was mined
    reverted: bool          # transaction was mined but reverted
    slippage_error_bps: float = 0.0   # |expected − realized| slippage in bps
    pnl_error_bps: float = 0.0        # |expected − realized| PnL in bps
    router: str = ""                  # target contract / router address


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------

class ExecutionStatsAccumulator:
    """Rolling-window accumulator for Feed E execution statistics.

    Maintains a deque of the last ``window_size`` :class:`ExecutionOutcome`
    records and computes a calibrated :class:`~apex_omega_core.core.domain_types.ExecutionStats`
    snapshot on demand.

    Parameters
    ----------
    window_size:
        Number of recent executions to retain in the rolling window.
        Default is 200.

    Thread safety
    -------------
    This class is *not* thread-safe.  In asyncio contexts all accesses
    happen on the event loop thread so no locking is required.  If used
    from multiple threads, wrap calls to :meth:`record` and :meth:`get_stats`
    with an external lock.
    """

    def __init__(self, window_size: int = 200) -> None:
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size}")
        self._window_size = window_size
        self._outcomes: Deque[ExecutionOutcome] = deque(maxlen=window_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        included: bool,
        reverted: bool = False,
        slippage_error_bps: float = 0.0,
        pnl_error_bps: float = 0.0,
        router: str = "",
    ) -> None:
        """Append a single execution outcome to the rolling window.

        Parameters
        ----------
        included:
            ``True`` when the transaction was mined (regardless of revert).
        reverted:
            ``True`` when the mined transaction reverted.  Only meaningful
            when ``included`` is also ``True``.
        slippage_error_bps:
            Absolute error between predicted and realized slippage, in basis
            points.  Pass ``0.0`` when unavailable.
        pnl_error_bps:
            Absolute error between predicted and realized PnL, in basis
            points.  Pass ``0.0`` when unavailable.
        router:
            Target contract address for per-router failure rate tracking.
        """
        self._outcomes.append(
            ExecutionOutcome(
                included=included,
                reverted=reverted,
                slippage_error_bps=float(slippage_error_bps),
                pnl_error_bps=float(pnl_error_bps),
                router=router.lower() if router else "",
            )
        )

    def record_from_invocation(
        self,
        invocation: dict,
        predicted_slippage_bps: float = 0.0,
        predicted_pnl_usd: float = 0.0,
        realized_pnl_usd: float = 0.0,
        router: str = "",
    ) -> None:
        """Convenience wrapper that extracts outcome fields from a :meth:`ContractInvoker.invoke` result dict.

        Parameters
        ----------
        invocation:
            The dict returned by ``ContractInvoker.invoke()``.
        predicted_slippage_bps:
            Slippage forecast from ``SlippageSentinel`` (bps).
        predicted_pnl_usd:
            Expected net profit from the pipeline (USD).
        realized_pnl_usd:
            Realized net profit measured after execution (USD).  When
            unavailable, pass ``0.0`` and ``pnl_error_bps`` will be 0.
        router:
            Target contract address.
        """
        included = bool(invocation.get("executed_onchain"))
        success = bool(invocation.get("success"))
        reverted = included and not success

        # Realized-vs-predicted PnL error in bps (capped at 10 000 bps).
        if predicted_pnl_usd > 0:
            pnl_error_bps = min(
                abs(predicted_pnl_usd - realized_pnl_usd) / predicted_pnl_usd * 10_000,
                10_000.0,
            )
        else:
            pnl_error_bps = 0.0

        self.record(
            included=included,
            reverted=reverted,
            slippage_error_bps=predicted_slippage_bps,
            pnl_error_bps=pnl_error_bps,
            router=router,
        )

    def get_stats(self) -> "ExecutionStats":
        """Return a live :class:`~apex_omega_core.core.domain_types.ExecutionStats` snapshot.

        Returns a snapshot with zero-filled fields when the window is empty
        so that callers can always safely call
        ``stats.p_exec_estimate()`` without guarding.
        """
        outcomes = list(self._outcomes)
        n = len(outcomes)

        if n == 0:
            return _make_stats(
                window_size=self._window_size,
                route_hit_rate=0.0,
                revert_rate=0.0,
                inclusion_rate=0.0,
                slippage_error_bps=0.0,
                pnl_error_bps=0.0,
                per_router_failure_rates={},
            )

        included_count = sum(1 for o in outcomes if o.included)
        reverted_count = sum(1 for o in outcomes if o.reverted)
        # route_hit_rate: fraction of cycles that produced an on-chain tx
        route_hit_rate = included_count / n
        # inclusion_rate: same as route_hit_rate in this context
        inclusion_rate = included_count / n
        # revert_rate: fraction of included txs that reverted
        revert_rate = reverted_count / max(included_count, 1) if included_count else 0.0
        # mean slippage and PnL error across all window entries
        slippage_error_bps = sum(o.slippage_error_bps for o in outcomes) / n
        pnl_error_bps = sum(o.pnl_error_bps for o in outcomes) / n

        # Per-router failure rate: fraction of invocations that did not succeed.
        router_totals: Dict[str, int] = {}
        router_failures: Dict[str, int] = {}
        for o in outcomes:
            r = o.router or "_unknown"
            router_totals[r] = router_totals.get(r, 0) + 1
            if not o.included or o.reverted:
                router_failures[r] = router_failures.get(r, 0) + 1

        per_router_failure_rates = {
            r: router_failures.get(r, 0) / total
            for r, total in router_totals.items()
        }

        return _make_stats(
            window_size=self._window_size,
            route_hit_rate=route_hit_rate,
            revert_rate=revert_rate,
            inclusion_rate=inclusion_rate,
            slippage_error_bps=slippage_error_bps,
            pnl_error_bps=pnl_error_bps,
            per_router_failure_rates=per_router_failure_rates,
        )

    @property
    def window_size(self) -> int:
        """Configured rolling-window size."""
        return self._window_size

    @property
    def sample_count(self) -> int:
        """Number of outcomes currently in the window."""
        return len(self._outcomes)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_stats(
    window_size: int,
    route_hit_rate: float,
    revert_rate: float,
    inclusion_rate: float,
    slippage_error_bps: float,
    pnl_error_bps: float,
    per_router_failure_rates: Dict[str, float],
) -> "ExecutionStats":
    """Construct an :class:`~apex_omega_core.core.domain_types.ExecutionStats` instance.

    Falls back to a minimal dict-like shim when the types module is not
    importable (e.g. in isolated unit tests).
    """
    try:
        from apex_omega_core.core.domain_types import ExecutionStats
        return ExecutionStats(
            window_size=window_size,
            route_hit_rate=route_hit_rate,
            revert_rate=revert_rate,
            inclusion_rate=inclusion_rate,
            realized_slippage_error_bps=slippage_error_bps,
            expected_vs_actual_pnl_error_bps=pnl_error_bps,
            per_router_failure_rates=per_router_failure_rates,
        )
    except ImportError:
        # Minimal shim for environments where the types module is unavailable.
        from dataclasses import dataclass as _dc, field as _f

        @_dc
        class _Stats:
            window_size: int
            route_hit_rate: float
            revert_rate: float
            inclusion_rate: float
            realized_slippage_error_bps: float
            expected_vs_actual_pnl_error_bps: float
            per_router_failure_rates: Dict[str, float] = _f(default_factory=dict)

            def p_exec_estimate(self) -> float:
                return max(0.0, min(1.0, self.inclusion_rate * (1.0 - self.revert_rate)))

        return _Stats(
            window_size=window_size,
            route_hit_rate=route_hit_rate,
            revert_rate=revert_rate,
            inclusion_rate=inclusion_rate,
            realized_slippage_error_bps=slippage_error_bps,
            expected_vs_actual_pnl_error_bps=pnl_error_bps,
            per_router_failure_rates=per_router_failure_rates,
        )
