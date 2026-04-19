"""Candidate-payload engine for C2 pre-execution candidate selection.

This module implements the Python strategy layer's candidate-selection
responsibility, as defined by the C1/C2 architecture boundary:

  * Python owns: candidate specs, validity windows, risk-adjusted EV
    scoring, block-by-block revalidation, and final STRIKE/DO_NOTHING
    selection.
  * Rust execution core owns: ABI encoding, RouteEnvelope construction,
    nonce lease, EIP-1559 tx build, signing, and relay submission.

``CandidateSelector`` is called from ``C2SurgeonApex.decide_contract_action``
and returns the winning ``PayloadCandidate`` spec (or ``None`` for
DO_NOTHING).  The spec is then handed to the Rust execution core, which
compiles it into a final signed payload.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# ValidityWindow
# ---------------------------------------------------------------------------

@dataclass
class ValidityWindow:
    """Off-chain validity window that gates a candidate without contract changes.

    A candidate is considered valid for blocks in the closed interval
    ``[start_block, end_block]``.  Using ``end_block = start_block + N``
    lets the engine model N+1..N+5 target blocks without touching on-chain
    state.
    """

    start_block: int
    end_block: int

    def is_valid_at(self, block_number: int) -> bool:
        """Return ``True`` when ``block_number`` falls inside the window."""
        return self.start_block <= block_number <= self.end_block

    def blocks_remaining(self, block_number: int) -> int:
        """Return how many blocks remain (0 if expired)."""
        return max(0, self.end_block - block_number)


# ---------------------------------------------------------------------------
# PayloadCandidate
# ---------------------------------------------------------------------------

@dataclass
class PayloadCandidate:
    """Candidate execution spec produced by the Python strategy layer.

    This is intentionally **not** a final payload: it carries no compiled
    calldata, ABI bytes, or signed transaction.  The Rust execution core
    receives this spec and compiles it into a ``RouteEnvelope`` and signed
    transaction.

    Fields
    ------
    candidate_id:
        Deterministic unique identifier for this candidate (UUIDv4 by default;
        callers may supply a stable ID when needed for deduplication).
    name:
        Human-readable label (e.g. ``"forward_strike"``, ``"reverse_strike"``).
    route_plan:
        Venue-level route description (list of hop dicts or a structured dict).
        The exact schema is router-family-specific; the Rust compiler interprets
        it when building ``RouteStep[]``.
    validity:
        Off-chain validity window for this candidate.
    base_ev:
        Gross expected value (profit) in USD at the time the candidate was
        created, before decay or risk penalties are applied.
    decay_rate:
        EV loss per block-offset from the creation block (e.g. ``0.05`` means
        5 % per block).  Applied multiplicatively.
    risk_penalty:
        Flat USD penalty subtracted after decay (encodes gas cost, slippage
        budget, suppression cost, etc.).
    min_profit:
        Minimum acceptable net profit in USD.  Candidates whose risk-adjusted
        EV falls below this are dropped during revalidation.
    size_hint_usd:
        Suggested notional trade size in USD.  The Rust compiler uses this as
        the flash-loan amount hint.
    route_kind:
        Semantic route kind consumed by the Rust compiler to select the
        appropriate codec (``"forward"``, ``"reverse"``, ``"duplicate"``).
    """

    name: str
    route_plan: object  # dict | list — intentionally untyped at this layer
    validity: ValidityWindow
    base_ev: float
    decay_rate: float
    risk_penalty: float
    min_profit: float
    size_hint_usd: float
    route_kind: str
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def risk_adjusted_ev(self, block_offset: int = 0) -> float:
        """Compute decay-and-penalty-adjusted EV at ``block_offset`` blocks from creation.

        EV(t) = base_ev × (1 − decay_rate) ^ block_offset − risk_penalty
        """
        decay_factor = max(0.0, 1.0 - self.decay_rate) ** max(0, block_offset)
        return self.base_ev * decay_factor - self.risk_penalty

    def is_profitable_at(self, block_offset: int = 0) -> bool:
        """Return ``True`` when risk-adjusted EV exceeds ``min_profit``."""
        return self.risk_adjusted_ev(block_offset) > self.min_profit


# ---------------------------------------------------------------------------
# CandidateSelector
# ---------------------------------------------------------------------------

class CandidateSelector:
    """C2 candidate-selection and validity-window engine.

    Responsibilities
    ----------------
    1. Build ``PayloadCandidate`` specs from sentinel optimizer output.
    2. Assign ``ValidityWindow`` instances (``[current_block, current_block + window_blocks]``).
    3. Revalidate each candidate every block: drop expired or unprofitable ones.
    4. Rank surviving candidates by risk-adjusted EV at the current offset.
    5. Return the best candidate spec, or ``None`` (→ DO_NOTHING).

    The selector does **not** build calldata, ABI payloads, or transactions.
    """

    #: Default number of future blocks over which a candidate remains valid.
    DEFAULT_WINDOW_BLOCKS: int = 5

    def __init__(self, window_blocks: int = DEFAULT_WINDOW_BLOCKS) -> None:
        self.window_blocks = window_blocks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_candidates(
        self,
        route: object,
        sentinel_output: dict,
        gas_cost: float,
        current_block: int,
        reverse_route: Optional[object] = None,
        reverse_output: Optional[dict] = None,
    ) -> List[PayloadCandidate]:
        """Build ``PayloadCandidate`` specs from C2 sentinel optimizer output.

        Parameters
        ----------
        route:
            Forward route as passed to the sentinel optimizer.
        sentinel_output:
            Dict returned by ``SlippageSentinel.build_c2_slippage_context``.
        gas_cost:
            Estimated gas cost in USD (used as base ``risk_penalty``).
        current_block:
            The block number at which candidates are being evaluated.
        reverse_route:
            Reverse route (if available).
        reverse_output:
            Sentinel output for the reverse route (if available).
        """
        candidates: List[PayloadCandidate] = []
        validity = ValidityWindow(
            start_block=current_block,
            end_block=current_block + self.window_blocks,
        )

        forward_ev = float(sentinel_output.get("profit", 0.0))
        optimal_input = float(sentinel_output.get("optimal_input", 0.0))
        slippage_total = sum(
            item.get("slippage", 0.0)
            for item in sentinel_output.get("slippage_per_leg", [])
        )
        slippage_penalty = slippage_total * optimal_input

        if forward_ev > 0.0:
            candidates.append(
                PayloadCandidate(
                    name="forward_strike",
                    route_plan=route,
                    validity=validity,
                    base_ev=forward_ev,
                    decay_rate=0.05,
                    risk_penalty=gas_cost + slippage_penalty,
                    min_profit=gas_cost,
                    size_hint_usd=optimal_input,
                    route_kind="forward",
                )
            )

        if reverse_route is not None and reverse_output is not None:
            reverse_ev = float(reverse_output.get("profit", 0.0))
            rev_optimal_input = float(reverse_output.get("optimal_input", 0.0))
            rev_slippage_total = sum(
                item.get("slippage", 0.0)
                for item in reverse_output.get("slippage_per_leg", [])
            )
            rev_slippage_penalty = rev_slippage_total * rev_optimal_input
            if reverse_ev > 0.0:
                candidates.append(
                    PayloadCandidate(
                        name="reverse_strike",
                        route_plan=reverse_route,
                        validity=validity,
                        base_ev=reverse_ev,
                        decay_rate=0.05,
                        risk_penalty=gas_cost + rev_slippage_penalty,
                        min_profit=gas_cost,
                        size_hint_usd=rev_optimal_input,
                        route_kind="reverse",
                    )
                )

            # Duplicate candidate: viable when forward EV is at least 2× gas cost.
            if forward_ev >= gas_cost * 2.0:
                candidates.append(
                    PayloadCandidate(
                        name="duplicate_strike",
                        route_plan=route,
                        validity=validity,
                        base_ev=forward_ev,
                        decay_rate=0.08,
                        risk_penalty=(gas_cost * 1.5) + slippage_penalty,
                        min_profit=gas_cost * 2.0,
                        size_hint_usd=optimal_input,
                        route_kind="duplicate",
                    )
                )

        return candidates

    def revalidate(
        self,
        candidates: List[PayloadCandidate],
        current_block: int,
        creation_block: int,
    ) -> List[PayloadCandidate]:
        """Return candidates that are still valid and profitable at ``current_block``.

        A candidate is dropped when:
        * its validity window has expired (``current_block > end_block``), or
        * its risk-adjusted EV at ``current_block - creation_block`` is at or
          below ``min_profit``.
        """
        block_offset = max(0, current_block - creation_block)
        surviving: List[PayloadCandidate] = []
        for c in candidates:
            if not c.validity.is_valid_at(current_block):
                continue
            if not c.is_profitable_at(block_offset):
                continue
            surviving.append(c)
        return surviving

    def select_best(
        self,
        candidates: List[PayloadCandidate],
        current_block: int,
        creation_block: int,
    ) -> Optional[PayloadCandidate]:
        """Return the highest-ranked surviving candidate, or ``None``.

        Ranking key: risk-adjusted EV at ``current_block - creation_block``,
        descending.  Ties are broken by ``candidate_id`` for determinism.
        """
        valid = self.revalidate(candidates, current_block, creation_block)
        if not valid:
            return None
        block_offset = max(0, current_block - creation_block)
        return max(
            valid,
            key=lambda c: (c.risk_adjusted_ev(block_offset), c.candidate_id),
        )
