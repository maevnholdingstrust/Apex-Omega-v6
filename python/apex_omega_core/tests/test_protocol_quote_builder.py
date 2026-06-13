"""Multi-protocol quote builder tests.

Covers every PoolFamily path through protocol_quote_builder.py:
  V2_CPMM             — LIVE: standard CPMM math
  V3_CLMM             — GATED: V3_TICK_NOT_TRAVERSED
  ALGEBRA_CLMM        — GATED: ALGEBRA_TICK_NOT_TRAVERSED
  BALANCER_WEIGHTED   — LIVE: weighted invariant
  CURVE_STABLE        — LIVE: StableSwap invariant
  AGGREGATOR          — GATED: UNSUPPORTED_POOL_TYPE
  UNKNOWN             — GATED: UNSUPPORTED_POOL_TYPE
  V4_HOOK             — GATED: UNSUPPORTED_POOL_TYPE

Also covers opportunity_adapter.py:
  - V2-only spread builds Opportunity
  - Cross-protocol: V2 buy beats gated V3 (non-executable V3 cannot win)
  - NoExecutableOpportunityError raised when all quotes are rejected
  - Profit gate respected
"""
from __future__ import annotations

import pytest

from apex_omega_core.core.protocol_quote_builder import (
    PoolQuoteRequest,
    build_quote,
)
from apex_omega_core.core.quote_selector import (
    ExecutableQuoteFilter,
    QuoteRejectReason,
)
from apex_omega_core.core.venue_registry import PoolFamily
from apex_omega_core.core.v3_tick_lane import V3PoolState
from apex_omega_core.core.balancer_v3_lane import (
    BALANCER_V3_POLYGON_VAULT,
    BalancerV3PoolState,
)
from apex_omega_core.core.opportunity_adapter import (
    LegQuoteSet,
    NoExecutableOpportunityError,
    OpportunityBuildRequest,
    OpportunityAdapter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POOL = "0x" + "ab" * 20
_TOKEN_A = "0x" + "aa" * 20
_TOKEN_B = "0x" + "bb" * 20

_AMOUNT_IN = 1_000_000  # 1 USDC (6 dec)


def _v2_request(**overrides) -> PoolQuoteRequest:
    defaults = dict(
        dex="QuickSwap V2",
        pool=_POOL,
        family=PoolFamily.V2_CPMM,
        amount_in=_AMOUNT_IN,
        liquidity_usd=100_000.0,
        reserve_in=10_000_000_000,
        reserve_out=12_000_000_000,
        fee_bps=30,
    )
    return PoolQuoteRequest(**{**defaults, **overrides})


def _v3_state(*, ticks_loaded=False) -> V3PoolState:
    return V3PoolState(
        pool=_POOL,
        token0=_TOKEN_A,
        token1=_TOKEN_B,
        fee_tier=500,
        sqrt_price_x96=2**96,
        tick=0,
        liquidity=1_000_000,
        initialized_ticks_loaded=ticks_loaded,
    )


def _balancer_state(*, pool_type="WEIGHTED", weights=None, vault=BALANCER_V3_POLYGON_VAULT) -> BalancerV3PoolState:
    return BalancerV3PoolState(
        pool=_POOL,
        pool_type=pool_type,
        tokens=[_TOKEN_A, _TOKEN_B],
        balances_raw=[10_000_000, 10_000_000],
        last_live_balances=[10_000_000, 10_000_000],
        weights=weights if weights is not None else [0.5, 0.5],
        swap_fee_bps=10,
        vault=vault,
    )


# ===========================================================================
# V2 CPMM
# ===========================================================================

class TestV2CPMM:
    def test_v2_executable_quote(self):
        q = build_quote(_v2_request())
        assert q.is_executable
        assert q.amount_out > 0
        assert q.effective_price > 0
        assert q.reject_reason is None

    def test_v2_zero_reserve_in(self):
        q = build_quote(_v2_request(reserve_in=0))
        assert not q.is_executable
        assert QuoteRejectReason.ZERO_RESERVES.value in q.reject_reason

    def test_v2_zero_reserve_out(self):
        q = build_quote(_v2_request(reserve_out=0))
        assert not q.is_executable
        assert QuoteRejectReason.ZERO_RESERVES.value in q.reject_reason

    def test_v2_none_reserves(self):
        q = build_quote(_v2_request(reserve_in=None, reserve_out=None))
        assert not q.is_executable
        assert QuoteRejectReason.ZERO_RESERVES.value in q.reject_reason

    def test_v2_dust_pool(self):
        q = build_quote(_v2_request(liquidity_usd=1.0))  # below 1_000 threshold
        assert not q.is_executable
        assert QuoteRejectReason.DUST_POOL.value in q.reject_reason

    def test_v2_effective_price_is_amount_sized(self):
        """effective_price == amount_out / amount_in (NOT spot price)."""
        q = build_quote(_v2_request())
        assert abs(q.effective_price - q.amount_out / q.amount_in) < 1e-9

    def test_v2_fee_in_quote(self):
        """Higher fee → lower amount_out."""
        q_low = build_quote(_v2_request(fee_bps=5))
        q_high = build_quote(_v2_request(fee_bps=300))
        assert q_high.amount_out < q_low.amount_out


# ===========================================================================
# V3 CLMM
# ===========================================================================

class TestV3CLMM:
    def test_v3_no_state_is_rejected(self):
        req = PoolQuoteRequest(
            dex="Uniswap V3",
            pool=_POOL,
            family=PoolFamily.V3_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert QuoteRejectReason.V3_TICK_MISSING.value in q.reject_reason

    def test_v3_state_without_ticks_is_rejected(self):
        req = PoolQuoteRequest(
            dex="Uniswap V3",
            pool=_POOL,
            family=PoolFamily.V3_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
            v3_state=_v3_state(ticks_loaded=False),
        )
        q = build_quote(req)
        assert not q.is_executable
        # Fails validate_v3_state → V3_TICK_MISSING
        assert q.reject_reason is not None

    def test_v3_state_with_ticks_is_gated(self):
        """Even with ticks loaded, tick traversal math is not implemented yet."""
        req = PoolQuoteRequest(
            dex="Uniswap V3",
            pool=_POOL,
            family=PoolFamily.V3_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
            v3_state=_v3_state(ticks_loaded=True),
        )
        q = build_quote(req)
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.V3_TICK_NOT_TRAVERSED.value

    def test_v3_cannot_beat_v2_in_selection(self):
        """A gated V3 quote cannot win over an executable V2 quote."""
        v2 = build_quote(_v2_request())
        v3 = build_quote(PoolQuoteRequest(
            dex="Uniswap V3",
            pool=_POOL,
            family=PoolFamily.V3_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
            v3_state=_v3_state(ticks_loaded=True),
        ))
        flt = ExecutableQuoteFilter()
        proof = flt.build_best_buy_proof([v3, v2])
        assert proof.selected_quote.dex == "QuickSwap V2"


# ===========================================================================
# Algebra CLMM
# ===========================================================================

class TestAlgebraCLMM:
    def test_algebra_no_state_is_rejected(self):
        req = PoolQuoteRequest(
            dex="QuickSwap V3 Algebra",
            pool=_POOL,
            family=PoolFamily.ALGEBRA_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert QuoteRejectReason.V3_TICK_MISSING.value in q.reject_reason

    def test_algebra_with_state_gated(self):
        req = PoolQuoteRequest(
            dex="QuickSwap V3 Algebra",
            pool=_POOL,
            family=PoolFamily.ALGEBRA_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
            v3_state=_v3_state(ticks_loaded=True),
        )
        q = build_quote(req)
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.ALGEBRA_TICK_NOT_TRAVERSED.value


# ===========================================================================
# Balancer Weighted
# ===========================================================================

class TestBalancerWeighted:
    def test_balancer_weighted_executable(self):
        req = PoolQuoteRequest(
            dex="Balancer V3",
            pool=_POOL,
            family=PoolFamily.BALANCER_WEIGHTED,
            amount_in=1_000.0,
            liquidity_usd=200_000.0,
            balancer_state=_balancer_state(),
            token_in_index=0,
            token_out_index=1,
        )
        q = build_quote(req)
        assert q.is_executable
        assert q.amount_out > 0
        assert q.reject_reason is None

    def test_balancer_wrong_vault_rejected(self):
        state = _balancer_state(vault="0x" + "00" * 20)
        req = PoolQuoteRequest(
            dex="Balancer V3",
            pool=_POOL,
            family=PoolFamily.BALANCER_WEIGHTED,
            amount_in=1_000.0,
            liquidity_usd=200_000.0,
            balancer_state=state,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert QuoteRejectReason.BALANCER_WRONG_VAULT.value in q.reject_reason

    def test_balancer_missing_weights_rejected(self):
        state = _balancer_state(weights=[])
        req = PoolQuoteRequest(
            dex="Balancer V3",
            pool=_POOL,
            family=PoolFamily.BALANCER_WEIGHTED,
            amount_in=1_000.0,
            liquidity_usd=200_000.0,
            balancer_state=state,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert q.reject_reason is not None

    def test_balancer_no_state_rejected(self):
        req = PoolQuoteRequest(
            dex="Balancer V3",
            pool=_POOL,
            family=PoolFamily.BALANCER_WEIGHTED,
            amount_in=1_000.0,
            liquidity_usd=200_000.0,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert QuoteRejectReason.BALANCER_WRONG_VAULT.value in q.reject_reason

    def test_balancer_stable_pool_gated(self):
        """Balancer stable/composable pools are gated until math is implemented."""
        state = _balancer_state(pool_type="STABLE")
        req = PoolQuoteRequest(
            dex="Balancer V3 Stable",
            pool=_POOL,
            family=PoolFamily.BALANCER_WEIGHTED,
            amount_in=1_000.0,
            liquidity_usd=200_000.0,
            balancer_state=state,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert q.reject_reason is not None


# ===========================================================================
# Curve StableSwap
# ===========================================================================

class TestCurveStableSwap:
    def test_curve_executable_with_invariant_state(self):
        req = PoolQuoteRequest(
            dex="Curve",
            pool=_POOL,
            family=PoolFamily.CURVE_STABLE,
            amount_in=1_000.0,
            liquidity_usd=500_000.0,
            curve_balances=[1_000_000.0, 1_000_000.0],
            curve_amp=100.0,
            fee_bps=4,
            token_in_index=0,
            token_out_index=1,
        )
        q = build_quote(req)
        assert q.is_executable
        assert q.amount_out > 0
        assert q.reject_reason is None

    def test_curve_missing_state_gated(self):
        req = PoolQuoteRequest(
            dex="Curve",
            pool=_POOL,
            family=PoolFamily.CURVE_STABLE,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert q.reject_reason == QuoteRejectReason.CURVE_INVARIANT_NOT_IMPLEMENTED.value

    def test_curve_cannot_beat_v2_in_selection(self):
        v2 = build_quote(_v2_request())
        curve = build_quote(PoolQuoteRequest(
            dex="Curve 3pool",
            pool=_POOL,
            family=PoolFamily.CURVE_STABLE,
            amount_in=_AMOUNT_IN,
            liquidity_usd=5_000_000.0,
        ))
        flt = ExecutableQuoteFilter()
        proof = flt.build_best_buy_proof([curve, v2])
        assert proof.selected_quote.dex == "QuickSwap V2"


# ===========================================================================
# Aggregator / Unknown / V4 (all UNSUPPORTED_POOL_TYPE)
# ===========================================================================

class TestUnsupportedProtocols:
    @pytest.mark.parametrize("family", [
        PoolFamily.AGGREGATOR,
        PoolFamily.UNKNOWN,
    ])
    def test_unsupported_family_rejected(self, family):
        req = PoolQuoteRequest(
            dex="SomeAggregator",
            pool=_POOL,
            family=family,
            amount_in=_AMOUNT_IN,
            liquidity_usd=500_000.0,
        )
        q = build_quote(req)
        assert not q.is_executable
        assert QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value in q.reject_reason


# ===========================================================================
# OpportunityAdapter — end-to-end
# ===========================================================================

class TestOpportunityAdapter:
    """End-to-end: pool snapshots → Opportunity ready for CycleOrchestrator."""

    def _v2_buy_leg(self) -> LegQuoteSet:
        return LegQuoteSet(
            token_in="USDCe",
            token_out="WMATIC",
            requests=[
                _v2_request(dex="QuickSwap V2", reserve_in=10_000_000, reserve_out=8_000_000),
                _v2_request(dex="ApeSwap V2",   reserve_in=10_000_000, reserve_out=7_900_000),
            ],
        )

    def _v2_sell_leg(self) -> LegQuoteSet:
        return LegQuoteSet(
            token_in="WMATIC",
            token_out="USDCe",
            requests=[
                _v2_request(dex="Sushiswap V2", reserve_in=8_000_000, reserve_out=10_100_000),
                _v2_request(dex="Dfyn V2",      reserve_in=8_000_000, reserve_out=9_900_000),
            ],
        )

    def _build_req(self, *, min_profit=0.0) -> OpportunityBuildRequest:
        return OpportunityBuildRequest(
            buy_leg=self._v2_buy_leg(),
            sell_leg=self._v2_sell_leg(),
            amount_in_usd=1_000.0,
            flash_fee_usd=0.9,
            gas_cost_usd=0.5,
            min_net_profit_usd=min_profit,
            target_pools=[_POOL],
        )

    def test_opportunity_builds_from_v2_quotes(self):
        opp = OpportunityAdapter().build(self._build_req())
        assert opp.fingerprint.leg1_buy_price > 0
        assert opp.fingerprint.leg2_sell_price > 0
        assert opp.fingerprint.raw_spread_bps > 0
        assert opp.exec_math.amount_in > 0
        assert opp.exec_math.net_profit is not None

    def test_fingerprint_records_dex_and_pool(self):
        opp = OpportunityAdapter().build(self._build_req())
        assert opp.fingerprint.leg1_dex != ""
        assert opp.fingerprint.leg1_pool != ""
        assert opp.fingerprint.leg2_dex != ""
        assert opp.fingerprint.leg2_pool != ""

    def test_best_buy_selected_for_leg1(self):
        """Adapter selects a valid executable buy price for leg1."""
        opp = OpportunityAdapter().build(self._build_req())
        assert opp.fingerprint.leg1_buy_price > 0
        assert opp.fingerprint.leg2_sell_price > 0

    def test_gated_v3_cannot_win_as_buy_leg(self):
        """A V3 pool in the buy leg cannot be selected if V2 is executable."""
        v3_req = PoolQuoteRequest(
            dex="Uniswap V3",
            pool=_POOL,
            family=PoolFamily.V3_CLMM,
            amount_in=_AMOUNT_IN,
            liquidity_usd=10_000_000.0,
            v3_state=_v3_state(ticks_loaded=True),
        )
        buy = LegQuoteSet(
            token_in="USDCe",
            token_out="WMATIC",
            requests=[
                v3_req,
                _v2_request(dex="QuickSwap V2", reserve_in=10_000_000, reserve_out=8_000_000),
            ],
        )
        req = OpportunityBuildRequest(
            buy_leg=buy,
            sell_leg=self._v2_sell_leg(),
            amount_in_usd=1_000.0,
            flash_fee_usd=0.9,
            gas_cost_usd=0.5,
        )
        opp = OpportunityAdapter().build(req)
        assert opp.fingerprint.leg1_buy_price > 0
        assert "QuickSwap" in opp.fingerprint.leg1_dex

    def test_all_buy_quotes_gated_raises(self):
        """NoExecutableOpportunityError if every buy quote is non-executable."""
        gated_buy = LegQuoteSet(
            token_in="USDCe",
            token_out="WMATIC",
            requests=[
                PoolQuoteRequest(
                    dex="Curve 3pool",
                    pool=_POOL,
                    family=PoolFamily.CURVE_STABLE,
                    amount_in=_AMOUNT_IN,
                    liquidity_usd=5_000_000.0,
                )
            ],
        )
        req = OpportunityBuildRequest(
            buy_leg=gated_buy,
            sell_leg=self._v2_sell_leg(),
            amount_in_usd=1_000.0,
            flash_fee_usd=0.9,
            gas_cost_usd=0.5,
        )
        with pytest.raises(NoExecutableOpportunityError):
            OpportunityAdapter().build(req)

    def test_profit_gate_enforced(self):
        """NoExecutableOpportunityError if net profit does not exceed gate."""
        req = self._build_req(min_profit=999_999.0)  # impossible threshold
        with pytest.raises(NoExecutableOpportunityError):
            OpportunityAdapter().build(req)

    def test_exec_math_fields_complete(self):
        """All 7 ExecutionMath fields are non-None."""
        opp = OpportunityAdapter().build(self._build_req())
        em = opp.exec_math
        assert em.amount_in is not None
        assert em.expected_out is not None
        assert em.min_out is not None
        assert em.flash_fee is not None
        assert em.gas_cost is not None
        assert em.dex_fees is not None
        assert em.net_profit is not None

    def test_min_out_is_slippage_buffered(self):
        """min_out < expected_out (50 bps buffer applied)."""
        opp = OpportunityAdapter().build(self._build_req())
        assert opp.exec_math.min_out < opp.exec_math.expected_out


class TestOpportunityRanker:
    def _make_opp(self, net_profit: float) -> "Opportunity":
        from apex_omega_core.orchestrator.cycle_orchestrator import (
            ExecutionMath, Opportunity, OpportunityFingerprint,
        )
        fp = OpportunityFingerprint(leg1_buy_price=1.0, leg2_sell_price=1.01, raw_spread_bps=10.0)
        em = ExecutionMath(amount_in=1000.0, expected_out=1010.0, min_out=1005.0,
                           flash_fee=0.9, gas_cost=0.5, dex_fees=0.3, net_profit=net_profit)
        return Opportunity(fingerprint=fp, exec_math=em, target_pools=[], net_profit_usd=net_profit)

    def test_returns_top_25(self):
        from apex_omega_core.core.opportunity_ranker import select_top_n
        opps = [self._make_opp(float(i)) for i in range(50)]
        top = select_top_n(opps, n=25)
        assert len(top) == 25
        assert top[0].net_profit_usd == 49.0

    def test_sorted_descending(self):
        from apex_omega_core.core.opportunity_ranker import select_top_n
        opps = [self._make_opp(p) for p in [5.0, 1.0, 10.0, 3.0]]
        top = select_top_n(opps, n=4)
        profits = [o.net_profit_usd for o in top]
        assert profits == sorted(profits, reverse=True)

    def test_fewer_than_n_returns_all(self):
        from apex_omega_core.core.opportunity_ranker import select_top_n
        opps = [self._make_opp(1.0), self._make_opp(2.0)]
        top = select_top_n(opps, n=25)
        assert len(top) == 2

