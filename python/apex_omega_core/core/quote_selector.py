"""Quote selection layer: proves leg1_buy_price is the lowest executable market price.

ONLY gate: TVL < $1,000 USD. All other venues and pairs are analyzed.

Design contract
---------------
Discovery MUST produce a ``BestBuyProof`` for every opportunity.  The proof
contains the full quote universe so any caller can independently verify that
``leg1_buy_price == min(effective_price for q in all_quotes if q.is_executable)``.

A lower *displayed* price does not count if the quote is non-executable (dust
pool, stale reserves, bad decimals, missing calldata, fork-sim mismatch, etc.).
Only the ``is_executable=True`` set participates in the selection.

The slippage gate (MAX_SLIPPAGE_BPS) has been removed. The only rejection paths
in ``make_quote`` are DUST_POOL (TVL < $1,000) and ZERO_RESERVES.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Rejection reasons (exhaustive; extend as new pool types are added)
# ---------------------------------------------------------------------------

class QuoteRejectReason(str, Enum):
    DUST_POOL = "DUST_POOL"
    ZERO_RESERVES = "ZERO_RESERVES"
    STALE_QUOTE = "STALE_QUOTE"
    BAD_DECIMALS = "BAD_DECIMALS"
    UNSUPPORTED_POOL_TYPE = "UNSUPPORTED_POOL_TYPE"
    QUOTE_TOO_SMALL = "QUOTE_TOO_SMALL"
    QUOTE_TOO_LARGE = "QUOTE_TOO_LARGE"
    MISSING_CALLDATA = "MISSING_CALLDATA"
    SLIPPAGE_EXCEEDS_GATE = "SLIPPAGE_EXCEEDS_GATE"
    FORK_SIM_MISMATCH = "FORK_SIM_MISMATCH"
    V3_TICK_MISSING = "V3_TICK_MISSING"
    POOL_NOT_REACHABLE = "POOL_NOT_REACHABLE"
    MID_PRICE_ONLY = "MID_PRICE_ONLY"
    # V3 / Algebra CLMM — tick traversal not yet implemented
    V3_TICK_NOT_TRAVERSED = "V3_TICK_NOT_TRAVERSED"
    ALGEBRA_TICK_NOT_TRAVERSED = "ALGEBRA_TICK_NOT_TRAVERSED"
    # Balancer — pool-level rejections
    BALANCER_WRONG_VAULT = "BALANCER_WRONG_VAULT"
    BALANCER_POOL_PAUSED = "BALANCER_POOL_PAUSED"
    BALANCER_MISSING_WEIGHTS = "BALANCER_MISSING_WEIGHTS"
    BALANCER_STABLE_GATED = "BALANCER_STABLE_GATED"
    # Curve — invariant not yet implemented
    CURVE_INVARIANT_NOT_IMPLEMENTED = "CURVE_INVARIANT_NOT_IMPLEMENTED"
    # UniswapV4 — hook dispatch not yet implemented
    V4_HOOK_NOT_IMPLEMENTED = "V4_HOOK_NOT_IMPLEMENTED"


# ---------------------------------------------------------------------------
# Core quote type
# ---------------------------------------------------------------------------

@dataclass
class ExecutableQuote:
    """A single DEX venue quote with executability verdict.

    ``effective_price`` is the *amount-sized* buy price: ``amount_out / amount_in``.
    It MUST be derived from a live AMM quote (``getAmountsOut`` / ``quoteExactIn``),
    not from a spot/mid-price display.  Higher ``effective_price`` = more expensive
    buy; lower = cheaper buy.

    Attributes
    ----------
    dex:
        Human-readable DEX identifier, e.g. ``"QuickSwap V2"``.
    pool:
        Checksum pool address.
    amount_in:
        Input token units (in the flashloan asset's decimals).
    amount_out:
        Expected output token units (in the target token's decimals).
    effective_price:
        ``amount_out / amount_in`` — the size-aware buy price.
    fee_bps:
        DEX swap fee expressed in basis points.
    slippage_bps:
        Estimated price impact vs. mid-price, in basis points.
    liquidity_usd:
        Pool TVL at quote time.
    is_executable:
        ``True`` iff all executability gates passed.
    reject_reason:
        Set when ``is_executable=False``; describes the gate that failed.
    block_number:
        Polygon block at which reserves were sampled.
    """

    dex: str
    pool: str
    amount_in: float
    amount_out: float
    effective_price: float
    fee_bps: float
    slippage_bps: float
    liquidity_usd: float
    is_executable: bool
    reject_reason: Optional[str] = None
    block_number: Optional[int] = None


# ---------------------------------------------------------------------------
# BestBuyProof — the attestation that leg1_buy_price is lowest executable
# ---------------------------------------------------------------------------

@dataclass
class BestBuyProof:
    """Evidence that ``selected_quote`` is the cheapest executable buy.

    Call ``assert_is_lowest_executable()`` to raise if the invariant is
    violated.  This should be called by the orchestrator before C1 encoding
    and by tests that verify discovery correctness.

    Attributes
    ----------
    selected_quote:
        The quote chosen by the discovery engine as ``leg1``.
    all_quotes:
        Complete quote universe considered at discovery time (executable and
        non-executable).
    rejected_count:
        Number of quotes that were non-executable.
    executable_count:
        Number of quotes that passed all executability gates.
    """

    selected_quote: ExecutableQuote
    all_quotes: List[ExecutableQuote]
    rejected_count: int
    executable_count: int

    def assert_is_lowest_executable(self) -> None:
        """Raise ``AssertionError`` if the selection is not the lowest executable price."""
        executables = [q for q in self.all_quotes if q.is_executable]
        if not executables:
            raise ValueError("BestBuyProof has no executable quotes — cannot assert lowest price")

        best = min(executables, key=lambda q: q.effective_price)
        if abs(self.selected_quote.effective_price - best.effective_price) > 1e-12:
            raise AssertionError(
                f"leg1_buy_price {self.selected_quote.effective_price:.10f} is not the "
                f"lowest executable market price {best.effective_price:.10f} "
                f"(better quote: dex={best.dex!r}, pool={best.pool!r})"
            )

    @property
    def leg1_buy_price(self) -> float:
        """Convenience accessor matching the discovery fingerprint field name."""
        return self.selected_quote.effective_price


@dataclass
class BestSellProof:
    """Evidence that ``selected_quote`` is the highest executable sell.

    Mirrors ``BestBuyProof`` but for leg2 where the best venue is the
    executable quote with the maximum effective price.
    """

    selected_quote: ExecutableQuote
    all_quotes: List[ExecutableQuote]
    rejected_count: int
    executable_count: int

    def assert_is_highest_executable(self) -> None:
        """Raise ``AssertionError`` if the selection is not the highest executable price."""
        executables = [q for q in self.all_quotes if q.is_executable]
        if not executables:
            raise ValueError("BestSellProof has no executable quotes — cannot assert highest price")

        best = max(executables, key=lambda q: q.effective_price)
        if abs(self.selected_quote.effective_price - best.effective_price) > 1e-12:
            raise AssertionError(
                f"leg2_sell_price {self.selected_quote.effective_price:.10f} is not the "
                f"highest executable market price {best.effective_price:.10f} "
                f"(better quote: dex={best.dex!r}, pool={best.pool!r})"
            )

    @property
    def leg2_sell_price(self) -> float:
        """Convenience accessor matching the discovery fingerprint field name."""
        return self.selected_quote.effective_price


# ---------------------------------------------------------------------------
# CandidateQuoteSet — the full universe for one token pair at one block
# ---------------------------------------------------------------------------

@dataclass
class CandidateQuoteSet:
    """Complete quote universe for one token pair captured at discovery time.

    Both ``buy_quotes`` and ``sell_quotes`` must be populated before
    ``ExecutableQuoteFilter.build_best_buy_proof`` is called.

    Attributes
    ----------
    token:
        Target token symbol, e.g. ``"WMATIC"``.
    amount_in:
        Flashloan amount used for all quotes in this set.
    buy_quotes:
        All sampled buy quotes (leg1 candidates).
    sell_quotes:
        All sampled sell quotes (leg2 candidates).
    block_number:
        Block at which the full set was sampled.
    """

    token: str
    amount_in: float
    buy_quotes: List[ExecutableQuote] = field(default_factory=list)
    sell_quotes: List[ExecutableQuote] = field(default_factory=list)
    block_number: Optional[int] = None


# ---------------------------------------------------------------------------
# ExecutableQuoteFilter — selection + proof construction
# ---------------------------------------------------------------------------

class ExecutableQuoteFilter:
    """Selects the cheapest executable buy and proves it.

    Constants
    ---------
    MIN_LIQUIDITY_USD:
        Quotes from pools below this TVL are rejected as ``DUST_POOL``.
        This is the ONLY discovery gate.
    """

    MIN_LIQUIDITY_USD: float = 1_000.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def filter_executable(self, quotes: List[ExecutableQuote]) -> List[ExecutableQuote]:
        """Return only quotes whose ``is_executable`` flag is ``True``."""
        return [q for q in quotes if q.is_executable]

    def build_best_buy_proof(self, quotes: List[ExecutableQuote]) -> BestBuyProof:
        """Select the lowest-priced executable buy quote and wrap in a proof.

        Parameters
        ----------
        quotes:
            Full candidate list (executable *and* non-executable).

        Returns
        -------
        BestBuyProof
            Proof whose ``selected_quote`` is the cheapest executable venue.

        Raises
        ------
        ValueError
            If no executable quotes exist in the candidate list.
        """
        executable = self.filter_executable(quotes)
        if not executable:
            raise ValueError(
                f"No executable buy quotes available — all {len(quotes)} quotes rejected. "
                "Check pool health, liquidity depth, and slippage gates."
            )

        best = min(executable, key=lambda q: q.effective_price)

        proof = BestBuyProof(
            selected_quote=best,
            all_quotes=list(quotes),
            rejected_count=len(quotes) - len(executable),
            executable_count=len(executable),
        )
        # Eagerly validate — fail fast at discovery rather than at execution.
        proof.assert_is_lowest_executable()
        return proof

    def build_best_sell_proof(self, quotes: List[ExecutableQuote]) -> BestSellProof:
        """Select the highest-priced executable sell quote and wrap in a proof.

        Parameters
        ----------
        quotes:
            Full candidate list (executable *and* non-executable).

        Returns
        -------
        BestSellProof
            Proof whose ``selected_quote`` is the highest executable sell venue.

        Raises
        ------
        ValueError
            If no executable quotes exist in the candidate list.
        """
        executable = self.filter_executable(quotes)
        if not executable:
            raise ValueError(
                f"No executable sell quotes available — all {len(quotes)} quotes rejected. "
                "Check pool health, liquidity depth, and slippage gates."
            )

        best = max(executable, key=lambda q: q.effective_price)

        proof = BestSellProof(
            selected_quote=best,
            all_quotes=list(quotes),
            rejected_count=len(quotes) - len(executable),
            executable_count=len(executable),
        )
        proof.assert_is_highest_executable()
        return proof

    def make_quote(
        self,
        *,
        dex: str,
        pool: str,
        amount_in: float,
        amount_out: float,
        fee_bps: float,
        liquidity_usd: float,
        block_number: Optional[int] = None,
    ) -> ExecutableQuote:
        """Construct an ``ExecutableQuote`` and apply executability gates.

        This is the **only** factory the discovery engine should use so that
        every rejection reason is recorded uniformly.
        """
        reject_reason: Optional[str] = None

        if amount_in <= 0 or amount_out <= 0:
            reject_reason = QuoteRejectReason.ZERO_RESERVES.value
        elif liquidity_usd < self.MIN_LIQUIDITY_USD:
            reject_reason = QuoteRejectReason.DUST_POOL.value
        else:
            # Amount-sized effective price: higher = more expensive buy.
            effective_price = amount_out / amount_in
            slippage_bps = 0.0

            return ExecutableQuote(
                dex=dex,
                pool=pool,
                amount_in=amount_in,
                amount_out=amount_out,
                effective_price=effective_price,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                liquidity_usd=liquidity_usd,
                is_executable=True,
                reject_reason=None,
                block_number=block_number,
            )

        # Non-executable path — still record the quote for the audit trail.
        safe_price = amount_out / amount_in if amount_in > 0 and amount_out > 0 else 0.0
        return ExecutableQuote(
            dex=dex,
            pool=pool,
            amount_in=amount_in,
            amount_out=amount_out,
            effective_price=safe_price,
            fee_bps=fee_bps,
            slippage_bps=0.0,
            liquidity_usd=liquidity_usd,
            is_executable=False,
            reject_reason=reject_reason,
            block_number=block_number,
        )
