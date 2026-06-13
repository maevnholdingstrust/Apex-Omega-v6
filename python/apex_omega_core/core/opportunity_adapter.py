"""Discovery-to-execution adapter (multi-protocol).

Converts raw pool snapshots — any protocol — into the ``Opportunity`` shape
that ``CycleOrchestrator.run_cycle()`` consumes.

Pipeline
--------
::

    [PoolQuoteRequest ×N]         (one per DEX / pool)
          │
          ▼
    ProtocolQuoteBuilder.build_quote()   per request
          │
          ▼
    filter is_executable=True
          │
          ├── buy leg:  min(effective_price)   → leg1_buy_price
          └── sell leg: max(effective_price)   → leg2_sell_price
          │
          ▼
    _build_execution_math()     → ExecutionMath
          │
          ▼
    _build_fingerprint()        → OpportunityFingerprint
          │
          ▼
    Opportunity                 → CycleOrchestrator.run_cycle()

Key invariants
--------------
* ``leg1_buy_price`` is the LOWEST effective price among all *executable* buy
  quotes.  Non-executable quotes (V3 gated, Curve gated, dust pools, …) are
  excluded from the selection.
* ``leg2_sell_price`` is the HIGHEST effective price among all *executable*
  sell quotes.
* ``net_profit_usd`` accounts for flash fee + gas + AMM fees.
* The adapter raises ``NoExecutableOpportunityError`` rather than returning a
  zero-profit Opportunity, so the caller can log and skip cleanly.
* Default ``min_net_profit_usd`` is ``-999_999.0`` — all pairs pass through;
  ranking/selection happens in the caller (e.g. opportunity_ranker.select_top_n).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .protocol_quote_builder import PoolQuoteRequest, build_quote
from .quote_selector import (
    ExecutableQuote,
    ExecutableQuoteFilter,
)
from ..orchestrator.cycle_orchestrator import (
    ExecutionMath,
    Opportunity,
    OpportunityFingerprint,
)


# ---------------------------------------------------------------------------
# Public error type
# ---------------------------------------------------------------------------

class NoExecutableOpportunityError(ValueError):
    """Raised when no profitable, executable arbitrage can be built."""


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------

@dataclass
class LegQuoteSet:
    """All pool quotes for one leg of the trade.

    Parameters
    ----------
    token_in:
        Input token symbol, e.g. ``"USDCe"``.
    token_out:
        Output token symbol, e.g. ``"WMATIC"``.
    requests:
        One ``PoolQuoteRequest`` per DEX / pool considered for this leg.
    """

    token_in: str
    token_out: str
    requests: List[PoolQuoteRequest] = field(default_factory=list)


@dataclass
class OpportunityBuildRequest:
    """Everything the adapter needs to build one ``Opportunity``.

    Parameters
    ----------
    buy_leg:
        Leg 1 — buy the target token.
    sell_leg:
        Leg 2 — sell the target token back to the flashloan asset.
    amount_in_usd:
        Size of the trade in USD (used for ExecutionMath).
    flash_fee_usd:
        Flashloan fee in USD (AAVE = ~0.09%, Balancer = 0%).
    gas_cost_usd:
        Estimated gas cost in USD at current gas price.
    min_net_profit_usd:
        Gate: reject if net_profit < this value. Defaults to -999_999.0
        (pass-through; ranking happens in opportunity_ranker.select_top_n).
    block_number:
        Block at which all quotes were sampled.
    target_pools:
        Pool addresses involved — passed through to ``Opportunity``.
    """

    buy_leg: LegQuoteSet
    sell_leg: LegQuoteSet
    amount_in_usd: float
    flash_fee_usd: float
    gas_cost_usd: float
    min_net_profit_usd: float = -999_999.0
    block_number: Optional[int] = None
    target_pools: List[str] = field(default_factory=list)
    lane_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OpportunityAdapter:
    """Converts multi-protocol pool quotes into an ``Opportunity``.

    The adapter is stateless — instantiate once and call ``build`` for each
    new set of quotes.

    Parameters
    ----------
    quote_filter:
        Optional custom filter to override MIN_LIQUIDITY_USD.
        Defaults to a new ``ExecutableQuoteFilter()`` with standard thresholds.
    """

    def __init__(self, quote_filter: Optional[ExecutableQuoteFilter] = None) -> None:
        self._filter = quote_filter or ExecutableQuoteFilter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, req: OpportunityBuildRequest) -> Opportunity:
        """Build an ``Opportunity`` from multi-protocol pool quotes.

        Parameters
        ----------
        req:
            ``OpportunityBuildRequest`` containing both legs, costs, and gates.

        Returns
        -------
        Opportunity
            Ready for ``CycleOrchestrator.run_cycle()``.

        Raises
        ------
        NoExecutableOpportunityError
            If no executable quotes exist for either leg, or if
            ``net_profit_usd <= min_net_profit_usd``.
        """
        buy_quotes = [build_quote(r) for r in req.buy_leg.requests]
        sell_quotes = [build_quote(r) for r in req.sell_leg.requests]
        try:
            buy_proof = self._filter.build_best_buy_proof(buy_quotes)
        except ValueError as exc:
            raise NoExecutableOpportunityError(
                f"No executable buy quotes for {req.buy_leg.token_in}→"
                f"{req.buy_leg.token_out}: all {len(buy_quotes)} quotes rejected"
            ) from exc

        try:
            sell_proof = self._filter.build_best_sell_proof(sell_quotes)
        except ValueError as exc:
            raise NoExecutableOpportunityError(
                f"No executable sell quotes for {req.sell_leg.token_in}→"
                f"{req.sell_leg.token_out}: all {len(sell_quotes)} quotes rejected"
            ) from exc

        best_buy: ExecutableQuote = buy_proof.selected_quote
        best_sell: ExecutableQuote = sell_proof.selected_quote

        exec_math, gross_profit, net_profit = self._build_execution_math(
            best_buy=best_buy,
            best_sell=best_sell,
            amount_in_usd=req.amount_in_usd,
            flash_fee_usd=req.flash_fee_usd,
            gas_cost_usd=req.gas_cost_usd,
        )

        if exec_math.net_profit <= req.min_net_profit_usd:
            raise NoExecutableOpportunityError(
                f"net_profit_usd {exec_math.net_profit:.6f} <= "
                f"min_net_profit_usd {req.min_net_profit_usd:.6f} — "
                f"spread after all costs is not enough"
            )

        raw_spread_bps = (
            (best_sell.effective_price - best_buy.effective_price)
            / best_buy.effective_price
            * 10_000.0
        ) if best_buy.effective_price > 0 else 0.0

        fingerprint = self._build_fingerprint(
            best_buy=best_buy,
            best_sell=best_sell,
            raw_spread_bps=raw_spread_bps,
        )

        return Opportunity(
            fingerprint=fingerprint,
            exec_math=exec_math,
            target_pools=list(req.target_pools),
            gross_profit_usd=gross_profit,
            net_profit_usd=net_profit,
            pair_id=f"{req.buy_leg.token_in}/{req.buy_leg.token_out}",
            lane_id=req.lane_id,
        )

    # ------------------------------------------------------------------
    # ExecutionMath builder
    # ------------------------------------------------------------------

    def _build_execution_math(
        self,
        *,
        best_buy: ExecutableQuote,
        best_sell: ExecutableQuote,
        amount_in_usd: float,
        flash_fee_usd: float,
        gas_cost_usd: float,
    ) -> tuple[ExecutionMath, float, float]:
        """Return (exec_math, gross_profit_usd, net_profit_usd)."""
        amount_in = best_buy.amount_in
        expected_out_buy = best_buy.amount_out
        expected_out_sell = best_sell.effective_price * expected_out_buy

        dex_fees_usd = (
            (best_buy.fee_bps + best_sell.fee_bps) / 10_000.0 * amount_in_usd
        )

        gross_profit = expected_out_sell - amount_in_usd
        net_profit = gross_profit - flash_fee_usd - gas_cost_usd - dex_fees_usd

        exec_math = ExecutionMath(
            amount_in=amount_in,
            expected_out=expected_out_sell,
            min_out=expected_out_sell * 0.995,  # 50 bps slippage buffer
            flash_fee=flash_fee_usd,
            gas_cost=gas_cost_usd,
            dex_fees=dex_fees_usd,
            net_profit=net_profit,
        )
        return exec_math, gross_profit, net_profit

    # ------------------------------------------------------------------
    # OpportunityFingerprint builder
    # ------------------------------------------------------------------

    def _build_fingerprint(
        self,
        *,
        best_buy: ExecutableQuote,
        best_sell: ExecutableQuote,
        raw_spread_bps: float,
    ) -> OpportunityFingerprint:
        return OpportunityFingerprint(
            leg1_buy_price=best_buy.effective_price,
            leg2_sell_price=best_sell.effective_price,
            raw_spread_bps=raw_spread_bps,
            leg1_dex=best_buy.dex,
            leg1_pool=best_buy.pool,
            leg2_dex=best_sell.dex,
            leg2_pool=best_sell.pool,
        )

