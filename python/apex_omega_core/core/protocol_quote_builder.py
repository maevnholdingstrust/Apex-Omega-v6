"""Protocol-agnostic AMM quote dispatcher.

Routes ``amount_out`` computation to the correct math module for each
``PoolFamily``.  Returns an ``ExecutableQuote`` — never raises.

Supported protocols
-------------------
V2_CPMM           UniswapV2-compatible CPMM           LIVE (full math)
V3_CLMM           UniswapV3 concentrated liquidity    GATED (tick traversal required)
ALGEBRA_CLMM      QuickSwap V3 / Algebra CLMM         GATED (same as V3)
BALANCER_WEIGHTED Balancer V2/V3 weighted pools       LIVE (weighted invariant)
CURVE_STABLE      Curve StableSwap                    GATED (invariant not implemented)
V4_HOOK           UniswapV4 hook pools                GATED (hook dispatch not implemented)
AGGREGATOR        External aggregators                NOT quotable off-chain
UNKNOWN           Always rejected

Gated protocols are intentionally gate-rejected so they can never silently win
the best-buy selection and produce bad execution.  Implement the underlying
math, remove the gate, and tests will enforce the new behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .quote_selector import ExecutableQuote, ExecutableQuoteFilter, QuoteRejectReason
from .venue_registry import PoolFamily
from .v2_cpmm_math import amount_out_cpmm
from .v3_tick_lane import V3PoolState, validate_v3_state
from .balancer_v3_lane import BalancerV3PoolState, quote_balancer_weighted_guarded
from .route_graph import _curve_get_dy


# ---------------------------------------------------------------------------
# Pool snapshot — unified input for any protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PoolQuoteRequest:
    """Unified quote request for any AMM protocol.

    Only the fields relevant to the declared ``family`` need to be provided.
    Missing required fields produce a gate-rejected quote rather than an
    exception.

    Parameters
    ----------
    dex:
        Human-readable DEX name, e.g. ``"QuickSwap V2"``.
    pool:
        Checksum pool/pair address.
    family:
        ``PoolFamily`` discriminator — determines which math module runs.
    amount_in:
        Input token amount (in the input token's raw integer units for V2;
        float is also accepted for Balancer weighted).
    liquidity_usd:
        Pool TVL at quote time (used for DUST_POOL gate).
    block_number:
        Block at which state was sampled.

    V2_CPMM fields:
        reserve_in, reserve_out, fee_bps

    V3_CLMM / ALGEBRA_CLMM fields:
        v3_state (``V3PoolState`` from ``v3_tick_lane``)
        fee_bps is read from v3_state.fee_tier

    BALANCER_WEIGHTED fields:
        balancer_state (``BalancerV3PoolState`` from ``balancer_v3_lane``)
        token_in_index, token_out_index

    CURVE_STABLE fields:
        curve_balances, curve_amp, token_in_index, token_out_index

    V4_HOOK fields:
        (gated — not yet implemented)
    """

    dex: str
    pool: str
    family: PoolFamily
    amount_in: float
    liquidity_usd: float
    block_number: Optional[int] = None

    # V2_CPMM
    reserve_in: Optional[int] = None
    reserve_out: Optional[int] = None
    fee_bps: int = 30

    # V3_CLMM / ALGEBRA_CLMM
    v3_state: Optional[V3PoolState] = None

    # BALANCER_WEIGHTED
    balancer_state: Optional[BalancerV3PoolState] = None
    token_in_index: int = 0
    token_out_index: int = 1

    # CURVE_STABLE
    curve_balances: Optional[list[float]] = None
    curve_amp: Optional[float] = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_filter = ExecutableQuoteFilter()


def build_quote(request: PoolQuoteRequest) -> ExecutableQuote:
    """Dispatch quote computation to the correct protocol handler.

    Always returns an ``ExecutableQuote``.  On any protocol-specific error or
    gate-rejection the returned quote has ``is_executable=False`` and a
    descriptive ``reject_reason``.  Never raises.

    The caller can pass its own ``ExecutableQuoteFilter`` instance to
    ``build_quote_with_filter`` if custom MIN_LIQUIDITY_USD / MAX_SLIPPAGE_BPS
    thresholds are needed.
    """
    return build_quote_with_filter(request, _filter)


def build_quote_with_filter(
    request: PoolQuoteRequest,
    quote_filter: ExecutableQuoteFilter,
) -> ExecutableQuote:
    """Same as ``build_quote`` but uses the provided filter instance."""
    try:
        amount_out, reject_reason = _dispatch(request)
    except Exception as exc:  # noqa: BLE001
        return _make_rejected(request, QuoteRejectReason.UNSUPPORTED_POOL_TYPE, str(exc))

    if reject_reason is not None:
        return _make_rejected(request, QuoteRejectReason(reject_reason))

    try:
        return quote_filter.make_quote(
            dex=request.dex,
            pool=request.pool,
            amount_in=float(request.amount_in),
            amount_out=float(amount_out),
            fee_bps=float(request.fee_bps),
            liquidity_usd=request.liquidity_usd,
            block_number=request.block_number,
        )
    except Exception as exc:  # noqa: BLE001
        return _make_rejected(request, QuoteRejectReason.UNSUPPORTED_POOL_TYPE, str(exc))


# ---------------------------------------------------------------------------
# Protocol dispatcher
# ---------------------------------------------------------------------------

def _dispatch(request: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    family = request.family

    if family == PoolFamily.V2_CPMM:
        return _quote_v2(request)
    if family == PoolFamily.V3_CLMM:
        return _quote_v3(request)
    if family == PoolFamily.ALGEBRA_CLMM:
        return _quote_algebra(request)
    if family == PoolFamily.BALANCER_WEIGHTED:
        return _quote_balancer(request)
    if family == PoolFamily.CURVE_STABLE:
        return _quote_curve(request)

    # V4_HOOK, AGGREGATOR, UNKNOWN — not quotable locally
    return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value


# ---------------------------------------------------------------------------
# V2 CPMM
# ---------------------------------------------------------------------------

def _quote_v2(req: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    if req.reserve_in is None or req.reserve_out is None:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value
    if req.reserve_in <= 0 or req.reserve_out <= 0:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value

    out = amount_out_cpmm(
        int(req.amount_in),
        int(req.reserve_in),
        int(req.reserve_out),
        req.fee_bps,
    )
    if out <= 0:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value
    return float(out), None


# ---------------------------------------------------------------------------
# V3 CLMM (UniswapV3-compatible)
# ---------------------------------------------------------------------------

def _quote_v3(req: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    if req.v3_state is None:
        return 0.0, QuoteRejectReason.V3_TICK_MISSING.value

    ok, reason = validate_v3_state(req.v3_state)
    if not ok:
        if "tick" in reason.lower():
            return 0.0, QuoteRejectReason.V3_TICK_MISSING.value
        return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value

    # Full initialized-tick traversal is required before V3 can produce
    # executable quotes.  Gate-reject here so a V3 pool can never silently
    # win over a V2 pool whose math is correct.
    return 0.0, QuoteRejectReason.V3_TICK_NOT_TRAVERSED.value


# ---------------------------------------------------------------------------
# Algebra CLMM (QuickSwap V3 / Camelot)
# ---------------------------------------------------------------------------

def _quote_algebra(req: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    """Algebra CLMM — same tick-traversal gate as UniswapV3."""
    if req.v3_state is None:
        return 0.0, QuoteRejectReason.V3_TICK_MISSING.value
    return 0.0, QuoteRejectReason.ALGEBRA_TICK_NOT_TRAVERSED.value


# ---------------------------------------------------------------------------
# Balancer V2 / V3 Weighted
# ---------------------------------------------------------------------------

def _quote_balancer(req: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    if req.balancer_state is None:
        return 0.0, QuoteRejectReason.BALANCER_WRONG_VAULT.value

    try:
        out = quote_balancer_weighted_guarded(
            state=req.balancer_state,
            token_in_index=req.token_in_index,
            token_out_index=req.token_out_index,
            amount_in=float(req.amount_in),
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "wrong_vault" in msg or "wrong vault" in msg:
            return 0.0, QuoteRejectReason.BALANCER_WRONG_VAULT.value
        if "paused" in msg:
            return 0.0, QuoteRejectReason.BALANCER_POOL_PAUSED.value
        if "weight" in msg:
            return 0.0, QuoteRejectReason.BALANCER_MISSING_WEIGHTS.value
        return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value
    except NotImplementedError:
        # Stable / composable-stable pools — gated until math is implemented
        return 0.0, QuoteRejectReason.BALANCER_STABLE_GATED.value

    if out <= 0:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value
    return float(out), None


# ---------------------------------------------------------------------------
# Curve StableSwap
# ---------------------------------------------------------------------------

def _quote_curve(req: PoolQuoteRequest) -> tuple[float, Optional[str]]:
    """Curve StableSwap quote using Newton invariant math."""
    if req.curve_balances is None or req.curve_amp is None:
        return 0.0, QuoteRejectReason.CURVE_INVARIANT_NOT_IMPLEMENTED.value
    if len(req.curve_balances) < 2:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value
    if req.token_in_index < 0 or req.token_out_index < 0:
        return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value
    if req.token_in_index >= len(req.curve_balances) or req.token_out_index >= len(req.curve_balances):
        return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value
    if req.token_in_index == req.token_out_index:
        return 0.0, QuoteRejectReason.UNSUPPORTED_POOL_TYPE.value
    if any(float(balance) <= 0.0 for balance in req.curve_balances):
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value

    out = _curve_get_dy(
        int(req.token_in_index),
        int(req.token_out_index),
        float(req.amount_in),
        [float(balance) for balance in req.curve_balances],
        float(req.curve_amp),
        float(req.fee_bps) / 10_000.0,
    )
    if out <= 0.0:
        return 0.0, QuoteRejectReason.ZERO_RESERVES.value
    return float(out), None


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _make_rejected(
    req: PoolQuoteRequest,
    reason: QuoteRejectReason,
    detail: str = "",
) -> ExecutableQuote:
    tag = f"{reason.value}:{detail}" if detail else reason.value
    return ExecutableQuote(
        dex=req.dex,
        pool=req.pool,
        amount_in=float(req.amount_in),
        amount_out=0.0,
        effective_price=0.0,
        fee_bps=float(req.fee_bps),
        slippage_bps=0.0,
        liquidity_usd=req.liquidity_usd,
        is_executable=False,
        reject_reason=tag,
        block_number=req.block_number,
    )
