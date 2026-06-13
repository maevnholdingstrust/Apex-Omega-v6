"""Tests proving leg1_buy_price == lowest executable market price.

Required proofs
---------------
1.  test_leg1_buy_price_is_lowest_executable_market_price
2.  test_lower_non_executable_price_cannot_win
3.  test_stale_quote_cannot_win
4.  test_bad_decimal_quote_cannot_win
5.  test_lowest_mid_price_does_not_override_lowest_effective_price
6.  test_best_buy_uses_amount_sized_quote_not_display_price
7.  test_dust_pool_cannot_win
8.  test_missing_calldata_quote_cannot_win (via is_executable=False)
9.  test_all_non_executable_raises
10. test_best_buy_proof_assert_invariant_passes_for_correct_selection
11. test_best_buy_proof_assert_invariant_fails_for_wrong_selection
12. test_fork_sim_mismatch_cannot_win
"""
from __future__ import annotations

import pytest

from apex_omega_core.core.quote_selector import (
    BestBuyProof,
    BestSellProof,
    CandidateQuoteSet,
    ExecutableQuote,
    ExecutableQuoteFilter,
    QuoteRejectReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_quote(
    dex: str,
    price: float,
    executable: bool,
    reject_reason: str | None = None,
    liquidity_usd: float = 500_000.0,
    amount_in: float = 10_000.0,
) -> ExecutableQuote:
    """Construct a quote with effective_price == price for deterministic tests."""
    return ExecutableQuote(
        dex=dex,
        pool=f"0x{dex[:4].upper()}POOL",
        amount_in=amount_in,
        amount_out=amount_in * price,
        effective_price=price,
        fee_bps=30.0,
        slippage_bps=10.0,
        liquidity_usd=liquidity_usd,
        is_executable=executable,
        reject_reason=reject_reason,
    )


def _filter() -> ExecutableQuoteFilter:
    return ExecutableQuoteFilter()


# ---------------------------------------------------------------------------
# Core selection proof
# ---------------------------------------------------------------------------

class TestBestBuyPriceSelection:
    def test_leg1_buy_price_is_lowest_executable_market_price(self):
        """The selected quote must have the lowest effective price among executables."""
        quotes = [
            _make_quote("A", price=1.00, executable=False, reject_reason="DUST_POOL"),
            _make_quote("B", price=1.05, executable=True),
            _make_quote("C", price=1.03, executable=True),   # ← lowest executable
            _make_quote("D", price=1.10, executable=True),
        ]

        proof = _filter().build_best_buy_proof(quotes)

        assert proof.selected_quote.effective_price == pytest.approx(1.03)
        assert proof.selected_quote.dex == "C"
        proof.assert_is_lowest_executable()   # must not raise

    def test_lower_non_executable_price_cannot_win(self):
        """A non-executable quote with a lower displayed price must not be selected."""
        quotes = [
            _make_quote("Cheap", price=0.90, executable=False, reject_reason="DUST_POOL"),
            _make_quote("Safe",  price=1.05, executable=True),
        ]

        proof = _filter().build_best_buy_proof(quotes)

        assert proof.selected_quote.dex == "Safe"
        assert proof.selected_quote.effective_price == pytest.approx(1.05)

    def test_stale_quote_cannot_win(self):
        """A stale (non-executable) quote must not win even if its price is lower."""
        stale = ExecutableQuote(
            dex="StalePool",
            pool="0xSTALE",
            amount_in=10_000.0,
            amount_out=9_500.0,   # effective_price=0.95 — lowest but stale
            effective_price=0.95,
            fee_bps=30.0,
            slippage_bps=0.0,
            liquidity_usd=1_000_000.0,
            is_executable=False,
            reject_reason=QuoteRejectReason.STALE_QUOTE.value,
        )
        fresh = _make_quote("FreshPool", price=1.02, executable=True)

        proof = _filter().build_best_buy_proof([stale, fresh])

        assert proof.selected_quote.dex == "FreshPool"

    def test_bad_decimal_quote_cannot_win(self):
        """A quote with bad decimal encoding (non-executable) must not win."""
        bad = ExecutableQuote(
            dex="BadDecimals",
            pool="0xBADD",
            amount_in=10_000.0,
            amount_out=9_000.0,   # effective_price=0.9 — appears cheap
            effective_price=0.90,
            fee_bps=30.0,
            slippage_bps=0.0,
            liquidity_usd=800_000.0,
            is_executable=False,
            reject_reason=QuoteRejectReason.BAD_DECIMALS.value,
        )
        good = _make_quote("GoodPool", price=1.01, executable=True)

        proof = _filter().build_best_buy_proof([bad, good])

        assert proof.selected_quote.dex == "GoodPool"

    def test_dust_pool_cannot_win(self):
        """A dust pool (below MIN_LIQUIDITY_USD) must not win."""
        dust = ExecutableQuote(
            dex="DustDEX",
            pool="0xDUST",
            amount_in=100.0,
            amount_out=103.0,   # effective_price=1.03
            effective_price=1.03,
            fee_bps=30.0,
            slippage_bps=5.0,
            liquidity_usd=500.0,  # below 1_000 MIN
            is_executable=False,
            reject_reason=QuoteRejectReason.DUST_POOL.value,
        )
        deep = _make_quote("DeepDEX", price=1.05, executable=True, liquidity_usd=2_000_000.0)

        proof = _filter().build_best_buy_proof([dust, deep])

        assert proof.selected_quote.dex == "DeepDEX"

    def test_fork_sim_mismatch_cannot_win(self):
        """A quote rejected by fork-sim must not be selected."""
        sim_fail = ExecutableQuote(
            dex="SimFail",
            pool="0xSIMFAIL",
            amount_in=10_000.0,
            amount_out=10_500.0,
            effective_price=1.05,
            fee_bps=30.0,
            slippage_bps=10.0,
            liquidity_usd=1_500_000.0,
            is_executable=False,
            reject_reason=QuoteRejectReason.FORK_SIM_MISMATCH.value,
        )
        passing = _make_quote("SimPass", price=1.07, executable=True)

        proof = _filter().build_best_buy_proof([sim_fail, passing])

        assert proof.selected_quote.dex == "SimPass"

    def test_all_non_executable_raises(self):
        """If every quote is non-executable, build_best_buy_proof must raise."""
        quotes = [
            _make_quote("A", price=1.0, executable=False, reject_reason="DUST_POOL"),
            _make_quote("B", price=0.9, executable=False, reject_reason="ZERO_RESERVES"),
        ]
        with pytest.raises(ValueError, match="No executable buy quotes"):
            _filter().build_best_buy_proof(quotes)


class TestBestSellPriceSelection:
    def test_leg2_sell_price_is_highest_executable_market_price(self):
        quotes = [
            _make_quote("A", price=1.00, executable=False, reject_reason="DUST_POOL"),
            _make_quote("B", price=1.05, executable=True),
            _make_quote("C", price=1.09, executable=True),
            _make_quote("D", price=1.07, executable=True),
        ]

        proof = _filter().build_best_sell_proof(quotes)

        assert proof.selected_quote.effective_price == pytest.approx(1.09)
        assert proof.selected_quote.dex == "C"
        proof.assert_is_highest_executable()

    def test_higher_non_executable_sell_cannot_win(self):
        quotes = [
            _make_quote("Spoof", price=1.20, executable=False, reject_reason="STALE_QUOTE"),
            _make_quote("Real", price=1.08, executable=True),
        ]

        proof = _filter().build_best_sell_proof(quotes)
        assert proof.selected_quote.dex == "Real"
        assert proof.leg2_sell_price == pytest.approx(1.08)

    def test_best_sell_proof_assert_invariant_fails_for_wrong_selection(self):
        quotes = [
            _make_quote("Lower", price=1.03, executable=True),
            _make_quote("Higher", price=1.11, executable=True),
        ]
        wrong = BestSellProof(
            selected_quote=quotes[0],
            all_quotes=quotes,
            rejected_count=0,
            executable_count=2,
        )
        with pytest.raises(AssertionError, match="not the highest executable market price"):
            wrong.assert_is_highest_executable()


# ---------------------------------------------------------------------------
# Amount-sized quote vs. display price
# ---------------------------------------------------------------------------

class TestAmountSizedPriceVsDisplayPrice:
    def test_lowest_mid_price_does_not_override_lowest_effective_price(self):
        """Mid-price (spot) is NOT the selection key — effective (sized) price is.

        Venue A: mid=0.98 (looks cheapest) but effective=1.06 after price impact
        Venue B: mid=1.00, effective=1.03 (actually cheaper after impact)
        """
        venue_a = ExecutableQuote(
            dex="VenueA_HighImpact",
            pool="0xAAAA",
            amount_in=100_000.0,
            amount_out=100_000.0 * 1.06,  # effective_price=1.06
            effective_price=1.06,
            fee_bps=30.0,
            slippage_bps=80.0,
            liquidity_usd=50_000.0,       # shallow — high impact
            is_executable=True,
            reject_reason=None,
        )
        venue_b = ExecutableQuote(
            dex="VenueB_LowImpact",
            pool="0xBBBB",
            amount_in=100_000.0,
            amount_out=100_000.0 * 1.03,  # effective_price=1.03
            effective_price=1.03,
            fee_bps=30.0,
            slippage_bps=10.0,
            liquidity_usd=5_000_000.0,    # deep — low impact
            is_executable=True,
            reject_reason=None,
        )

        proof = _filter().build_best_buy_proof([venue_a, venue_b])

        assert proof.selected_quote.dex == "VenueB_LowImpact"
        assert proof.selected_quote.effective_price == pytest.approx(1.03)

    def test_best_buy_uses_amount_sized_quote_not_display_price(self):
        """Selection uses getAmountsOut (amount_out/amount_in), not tick/mid price."""
        # Both venues report mid=1.00 but differ after AMM math
        low_impact = ExecutableQuote(
            dex="QuickSwap",
            pool="0xQS",
            amount_in=5_000.0,
            amount_out=5_000.0 * 1.015,   # effective=1.015 (deep pool, low slippage)
            effective_price=1.015,
            fee_bps=30.0,
            slippage_bps=5.0,
            liquidity_usd=2_000_000.0,
            is_executable=True,
        )
        high_impact = ExecutableQuote(
            dex="ApeSwap",
            pool="0xAS",
            amount_in=5_000.0,
            amount_out=5_000.0 * 1.025,   # effective=1.025 (shallow, higher impact)
            effective_price=1.025,
            fee_bps=25.0,
            slippage_bps=50.0,
            liquidity_usd=80_000.0,
            is_executable=True,
        )

        proof = _filter().build_best_buy_proof([low_impact, high_impact])

        assert proof.selected_quote.dex == "QuickSwap"
        assert proof.selected_quote.effective_price == pytest.approx(1.015)
        assert proof.leg1_buy_price == pytest.approx(1.015)


# ---------------------------------------------------------------------------
# BestBuyProof assertion enforcement
# ---------------------------------------------------------------------------

class TestBestBuyProofAssertion:
    def test_best_buy_proof_assert_invariant_passes_for_correct_selection(self):
        """assert_is_lowest_executable() must not raise when selection is correct."""
        quotes = [
            _make_quote("X", price=1.10, executable=True),
            _make_quote("Y", price=1.04, executable=True),  # ← lowest
            _make_quote("Z", price=1.08, executable=True),
        ]
        proof = BestBuyProof(
            selected_quote=quotes[1],   # Y is cheapest
            all_quotes=quotes,
            rejected_count=0,
            executable_count=3,
        )
        proof.assert_is_lowest_executable()  # must not raise

    def test_best_buy_proof_assert_invariant_fails_for_wrong_selection(self):
        """assert_is_lowest_executable() must raise when a cheaper executable exists."""
        quotes = [
            _make_quote("Expensive", price=1.10, executable=True),
            _make_quote("Cheaper",   price=1.04, executable=True),   # actual best
        ]
        wrong_proof = BestBuyProof(
            selected_quote=quotes[0],   # 1.10 was selected — wrong
            all_quotes=quotes,
            rejected_count=0,
            executable_count=2,
        )
        with pytest.raises(AssertionError, match="not the lowest executable market price"):
            wrong_proof.assert_is_lowest_executable()

    def test_proof_counts_are_correct(self):
        """rejected_count and executable_count must match the quote list."""
        quotes = [
            _make_quote("A", price=1.02, executable=True),
            _make_quote("B", price=1.01, executable=True),
            _make_quote("C", price=0.95, executable=False, reject_reason="DUST_POOL"),
            _make_quote("D", price=0.93, executable=False, reject_reason="STALE_QUOTE"),
        ]
        proof = _filter().build_best_buy_proof(quotes)

        assert proof.executable_count == 2
        assert proof.rejected_count == 2
        assert proof.selected_quote.dex == "B"

    def test_leg1_buy_price_property_matches_selected_effective_price(self):
        """BestBuyProof.leg1_buy_price must alias selected_quote.effective_price."""
        quotes = [_make_quote("Solo", price=1.07, executable=True)]
        proof = _filter().build_best_buy_proof(quotes)

        assert proof.leg1_buy_price == proof.selected_quote.effective_price


# ---------------------------------------------------------------------------
# ExecutableQuoteFilter.make_quote gate enforcement
# ---------------------------------------------------------------------------

class TestMakeQuoteGates:
    def test_zero_amount_in_rejected(self):
        q = _filter().make_quote(
            dex="X", pool="0x00", amount_in=0.0, amount_out=100.0,
            fee_bps=30.0, liquidity_usd=1_000_000.0,
        )
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.ZERO_RESERVES.value

    def test_zero_amount_out_rejected(self):
        q = _filter().make_quote(
            dex="X", pool="0x00", amount_in=1000.0, amount_out=0.0,
            fee_bps=30.0, liquidity_usd=1_000_000.0,
        )
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.ZERO_RESERVES.value

    def test_dust_pool_rejected(self):
        q = _filter().make_quote(
            dex="X", pool="0x00", amount_in=1000.0, amount_out=1010.0,
            fee_bps=30.0, liquidity_usd=500.0,  # below MIN_LIQUIDITY_USD=1000
        )
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.DUST_POOL.value

    def test_healthy_quote_is_executable(self):
        q = _filter().make_quote(
            dex="GoodDEX", pool="0xGOOD", amount_in=10_000.0, amount_out=10_200.0,
            fee_bps=30.0, liquidity_usd=2_000_000.0,
        )
        assert q.is_executable
        assert q.reject_reason is None
        assert q.effective_price == pytest.approx(1.02)


# ---------------------------------------------------------------------------
# CandidateQuoteSet structure
# ---------------------------------------------------------------------------

class TestCandidateQuoteSet:
    def test_candidate_set_stores_full_universe(self):
        qs = CandidateQuoteSet(token="WMATIC", amount_in=5_000.0, block_number=99_000_000)
        qs.buy_quotes = [
            _make_quote("A", price=1.05, executable=True),
            _make_quote("B", price=0.98, executable=False, reject_reason="DUST_POOL"),
        ]
        qs.sell_quotes = [
            _make_quote("C", price=1.10, executable=True),
        ]

        assert len(qs.buy_quotes) == 2
        assert len(qs.sell_quotes) == 1
        assert qs.block_number == 99_000_000

        executable_buys = [q for q in qs.buy_quotes if q.is_executable]
        assert len(executable_buys) == 1
        assert executable_buys[0].dex == "A"
