"""Expanded graph scan: N-hop multi-DEX arbitrage using RouteGraph.

This module is the public-facing driver for multi-hop cycle discovery.  It
wraps :mod:`route_graph` with:

* Fork-readiness gate — **fails closed**.  When ``fork_safe=True`` (the
  default), any step that requires live secrets (RPC, private key, relay
  submission) is blocked.  Callers that need live execution must pass
  ``fork_safe=False`` explicitly and gate that on a protected CI environment.
* Structured result types — :class:`ScoredRoute`, :class:`ScanCandidate`,
  and :class:`ExpandedGraphScanResult` mirror the scanner surface contract so
  every downstream consumer (dry_run, app.py, dashboard) can read the same
  fields without re-interpreting raw records.
* Cycle deduplication — symmetric cycles (A→B→C→A and A→C→B→A) are
  considered equivalent; only the more profitable direction is kept.

Public API
----------
ScoredRoute
    A single N-hop cycle with full profitability metrics.
ScanCandidate
    A ScoredRoute promoted for execution, with gate metadata.
ExpandedGraphScanResult
    Full result bundle returned by :func:`expanded_graph_scan`.
expanded_graph_scan
    Main entry point.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .route_graph import CycleRecord, RouteGraph, scan_multi_hop_cycles

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gate reasons
# ---------------------------------------------------------------------------

_GATE_OK = "ok"
_GATE_FORK_BLOCKED = "fork_safe: live execution disabled in fork/PR CI"
_GATE_UNPROFITABLE = "net_profit_usd <= 0"
_GATE_LOW_P_FILL = "p_fill below minimum threshold"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScoredRoute:
    """A single N-hop cycle with full profitability metrics.

    Wraps :class:`~route_graph.CycleRecord` and adds a human-readable
    ``route_label`` and a ``fork_safe`` flag for downstream execution checks.
    """
    cycle: CycleRecord
    route_id: str       # deterministic hash of the token+pool sequence
    route_label: str    # human-readable, e.g. "WMATIC→USDC→WETH→WMATIC"
    fork_safe: bool     # True when the record was produced without live secrets

    # Convenience pass-throughs (avoids deep attribute access in templates)
    @property
    def hop_count(self) -> int:
        return self.cycle.hop_count

    @property
    def net_profit_usd(self) -> float:
        return self.cycle.net_profit_usd

    @property
    def e_profit(self) -> float:
        return self.cycle.e_profit

    @property
    def p_fill(self) -> float:
        return self.cycle.p_fill

    @property
    def trade_size_usd(self) -> float:
        return self.cycle.trade_size_usd

    @property
    def profitable(self) -> bool:
        return self.cycle.profitable


@dataclass
class ScanCandidate:
    """A :class:`ScoredRoute` evaluated by the execution gate.

    ``execution_ready`` is True only when all of the following hold:

    * ``fork_safe`` is False (or the scan was explicitly allowed to execute).
    * ``net_profit_usd > 0``.
    * ``p_fill >= min_p_fill``.
    * No other gate condition fired.

    When ``execution_ready`` is False, ``gate_reason`` explains why.
    """
    scored_route: ScoredRoute
    execution_ready: bool
    gate_reason: str    # _GATE_OK or one of the _GATE_* constants above


@dataclass
class ExpandedGraphScanResult:
    """Full result bundle returned by :func:`expanded_graph_scan`.

    Attributes
    ----------
    scan_timestamp
        Unix timestamp (float) at the start of the scan.
    candidates
        All :class:`ScanCandidate` objects produced, including non-executable
        ones.  Sorted by ``e_profit`` descending.
    total_cycles_evaluated
        Total number of unique N-hop cycles that were simulated.
    profitable_cycles
        Number of cycles that passed ``min_net_profit_usd``.
    top_candidate
        The highest-``e_profit`` *execution-ready* candidate, or ``None``
        when none passed the gate.
    hop_range
        ``(min_hops, max_hops)`` that were searched.
    elapsed_seconds
        Wall-clock time for the scan (excluding pool discovery).
    """
    scan_timestamp: float
    candidates: List[ScanCandidate]
    total_cycles_evaluated: int
    profitable_cycles: int
    top_candidate: Optional[ScanCandidate]
    hop_range: tuple  # (min_hops, max_hops)
    elapsed_seconds: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route ID helper
# ---------------------------------------------------------------------------

def _route_id(cycle: CycleRecord) -> str:
    """Deterministic identifier for a cycle (token sequence + pool sequence).

    Returns a ``rg_`` prefixed 12-character hex hash derived from the full
    token and pool sequence.
    """
    tokens_part = ":".join(cycle.tokens)
    pools_part = ":".join(cycle.pools)
    raw = f"{tokens_part}|{pools_part}"
    import hashlib
    return "rg_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _route_label(cycle: CycleRecord) -> str:
    """Human-readable label, e.g. 'WMATIC→USDC→WETH→WMATIC'."""
    return "→".join(cycle.tokens)


# ---------------------------------------------------------------------------
# Cycle deduplication
# ---------------------------------------------------------------------------

def _minimal_rotation(tokens: List[str]) -> tuple[str, ...]:
    """Return the lexicographically smallest cyclic rotation for ``tokens``."""
    if not tokens:
        return ()
    rotations = [
        tuple(tokens[i:] + tokens[:i])
        for i in range(len(tokens))
    ]
    return min(rotations)


def _canonical_cycle_key(tokens: List[str]) -> tuple[str, ...]:
    """Return a canonical key that treats forward and reverse cycles as equal.

    For a 3-hop cycle A→B→C→A, the canonical key preserves token order while
    normalizing across cyclic rotations and reverse traversal. This means the
    scan keeps at most one key for the same cycle regardless of starting point
    or direction, without collapsing distinct non-symmetric cycles that happen
    to use the same token set.
    """
    # Exclude the repeated start token at the tail when the cycle is closed.
    interior = tokens[:-1] if len(tokens) > 1 and tokens[0] == tokens[-1] else tokens[:]
    if len(interior) <= 1:
        return tuple(interior)

    forward_key = _minimal_rotation(interior)
    reverse_key = _minimal_rotation(list(reversed(interior)))
    return min(forward_key, reverse_key)
# ---------------------------------------------------------------------------
# Execution gate
# ---------------------------------------------------------------------------

def _apply_gate(
    scored: ScoredRoute,
    fork_safe: bool,
    min_p_fill: float,
) -> ScanCandidate:
    """Evaluate execution readiness for a single scored route.

    The gate is **fail-closed**: any doubt results in ``execution_ready=False``.
    """
    if fork_safe:
        return ScanCandidate(
            scored_route=scored,
            execution_ready=False,
            gate_reason=_GATE_FORK_BLOCKED,
        )
    if scored.net_profit_usd <= 0.0:
        return ScanCandidate(
            scored_route=scored,
            execution_ready=False,
            gate_reason=_GATE_UNPROFITABLE,
        )
    if scored.p_fill < min_p_fill:
        return ScanCandidate(
            scored_route=scored,
            execution_ready=False,
            gate_reason=_GATE_LOW_P_FILL,
        )
    return ScanCandidate(
        scored_route=scored,
        execution_ready=True,
        gate_reason=_GATE_OK,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def expanded_graph_scan(
    pool_map: Dict[str, List[Any]],
    token_prices: Dict[str, float],
    tip_optimizer: Any,
    min_hops: int = 2,
    max_hops: int = 4,
    max_trade_size_usd: float = 10_000.0,
    flash_loan_fee_rate: float = 0.0,
    min_net_profit_usd: float = 1.0,
    min_p_fill: float = 0.1,
    fork_safe: bool = True,
    deduplicate_symmetric: bool = True,
    gas_units_multiplier: float = 1.0,
    grid_points: int = 16,
    metadata: Optional[Dict[str, Any]] = None,
) -> ExpandedGraphScanResult:
    """Run an expanded N-hop graph scan and return a structured result.

    Parameters
    ----------
    pool_map
        ``{pair_key: [pool, …]}`` dict from pool discovery.
    token_prices
        USD price per token symbol.
    tip_optimizer
        :class:`~apex_omega_core.core.mev_gas_oracle.TipOptimizer` instance.
    min_hops
        Minimum swaps per cycle (2 = two-leg).
    max_hops
        Maximum swaps per cycle (bounded to 6 internally to prevent explosion).
    max_trade_size_usd
        Upper cap on trade size.
    flash_loan_fee_rate
        Flash-loan fee as a decimal (0.0 = no fee, 0.0009 = Aave V3).
    min_net_profit_usd
        Minimum net profit to include a cycle in the results.
    min_p_fill
        Minimum P(fill) threshold for ``execution_ready`` classification.
    fork_safe
        **Fail-closed execution gate.**  When ``True`` (the default), no
        candidate will have ``execution_ready=True``.  Set to ``False`` only
        in protected execution environments (``prod`` GitHub environment with
        approval gates).
    deduplicate_symmetric
        When ``True``, suppress the weaker direction of symmetric cycle pairs
        (e.g. A→B→C→A vs A→C→B→A) — keeps only the more profitable one.
    gas_units_multiplier
        Multiplied into the per-hop gas cost estimate.
    grid_points
        Number of input-size grid points for the profit search.
    metadata
        Arbitrary key/value pairs passed through to the result's ``metadata``
        field (e.g. scan ID, block number).

    Returns
    -------
    ExpandedGraphScanResult
    """
    t_start = time.monotonic()
    ts = time.time()

    # Clamp max_hops to a safe upper bound
    max_hops = min(max(max_hops, min_hops), 6)

    raw_records: List[CycleRecord] = scan_multi_hop_cycles(
        pool_map=pool_map,
        token_prices=token_prices,
        tip_optimizer=tip_optimizer,
        min_hops=min_hops,
        max_hops=max_hops,
        max_trade_size_usd=max_trade_size_usd,
        flash_loan_fee_rate=flash_loan_fee_rate,
        min_net_profit_usd=min_net_profit_usd,
        gas_units_multiplier=gas_units_multiplier,
        grid_points=grid_points,
    )

    # Optionally deduplicate symmetric cycles
    if deduplicate_symmetric:
        seen_keys: set = set()
        deduped: List[CycleRecord] = []
        for rec in raw_records:
            key = _canonical_cycle_key(rec.tokens)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(rec)
        raw_records = deduped

    profitable_count = len(raw_records)

    # Build ScoredRoute + ScanCandidate for each
    candidates: List[ScanCandidate] = []
    for rec in raw_records:
        scored = ScoredRoute(
            cycle=rec,
            route_id=_route_id(rec),
            route_label=_route_label(rec),
            fork_safe=fork_safe,
        )
        candidate = _apply_gate(scored, fork_safe=fork_safe, min_p_fill=min_p_fill)
        candidates.append(candidate)

    # Sort by e_profit descending (scan_multi_hop_cycles already does this, but
    # deduplication may have changed the order slightly)
    candidates.sort(key=lambda c: c.scored_route.e_profit, reverse=True)

    top = next(
        (c for c in candidates if c.execution_ready),
        None,
    )

    elapsed = time.monotonic() - t_start
    logger.debug(
        "expanded_graph_scan: %d profitable cycles in %.3fs "
        "(fork_safe=%s, hops=%d..%d)",
        profitable_count,
        elapsed,
        fork_safe,
        min_hops,
        max_hops,
    )

    return ExpandedGraphScanResult(
        scan_timestamp=ts,
        candidates=candidates,
        total_cycles_evaluated=profitable_count,
        profitable_cycles=profitable_count,
        top_candidate=top,
        hop_range=(min_hops, max_hops),
        elapsed_seconds=elapsed,
        metadata=metadata or {},
    )
