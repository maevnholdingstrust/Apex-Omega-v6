from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from .market_surface_labels import classify_flash_ladder_zone, is_size_zone_allowed_for_c1
from .multi_market_scanner import quotes_for_pair
from .polygon_market_registry import TOKENS

Protocol = Literal["v2", "v3", "curve", "balancer", "dodo", "hybrid", "algebra"]


@dataclass(frozen=True)
class ExecutableMarketPoint:
    chain_id: int
    block_number: int
    token_in: str
    token_out: str
    token_in_decimals: int
    token_out_decimals: int
    venue: str
    protocol: str
    pool: str
    router: str | None
    fee_bps: float
    amount_in_raw: str
    amount_out_raw: str
    amount_in_human: float
    amount_out_human: float
    effective_price: float
    spot_price: float | None
    price_impact_bps: float
    liquidity_usd: float
    quote_fresh_ms: float
    confidence: float
    executable: bool


@dataclass(frozen=True)
class SizeLadderPoint:
    amount_in_human: float
    buy_out: float
    sell_out: float
    gross_profit_usd: float
    estimated_cost_usd: float
    net_profit_usd: float
    executable_spread_bps: float
    fraction_of_leg1_tvl: float
    zone: str


@dataclass(frozen=True)
class MarketDistanceOpportunity:
    id: str
    chain_id: int
    pair: str
    base_token: str
    quote_token: str
    block_number: int
    best_buy: ExecutableMarketPoint
    best_sell: ExecutableMarketPoint
    raw_spread_bps: float
    executable_spread_bps: float
    gas_adjusted_spread_bps: float
    size_ladder: tuple[SizeLadderPoint, ...]
    c1_candidate: bool
    c2_decision: str
    reject_reason: str | None = None


def _cpmm_out(amount_in: float, reserve_in: float, reserve_out: float, fee_bps: float) -> float:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    x_eff = amount_in * (1.0 - fee_bps / 10_000.0)
    return (x_eff * reserve_out) / (reserve_in + x_eff)


def _market_id(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _point_from_quote(*, quote, token_in: str, token_out: str, amount_in: float, amount_out: float, block_number: int, quote_fresh_ms: float = 0.0) -> ExecutableMarketPoint:
    token_in_spec = TOKENS[token_in]
    token_out_spec = TOKENS[token_out]
    effective_price = amount_out / amount_in if amount_in > 0 else 0.0
    spot = quote.price_quote_per_base
    impact = ((spot - effective_price) / spot * 10_000.0) if spot else 0.0
    confidence = 1.0
    if quote.liquidity_hint <= 0:
        confidence = 0.0
    elif abs(impact) > 250:
        confidence = 0.5
    return ExecutableMarketPoint(
        chain_id=137,
        block_number=block_number,
        token_in=token_in_spec.address,
        token_out=token_out_spec.address,
        token_in_decimals=token_in_spec.decimals,
        token_out_decimals=token_out_spec.decimals,
        venue=quote.venue,
        protocol=quote.kind,
        pool=quote.pool,
        router=None,
        fee_bps=quote.fee_bps,
        amount_in_raw=str(int(amount_in * (10 ** token_in_spec.decimals))),
        amount_out_raw=str(int(amount_out * (10 ** token_out_spec.decimals))),
        amount_in_human=amount_in,
        amount_out_human=amount_out,
        effective_price=effective_price,
        spot_price=spot,
        price_impact_bps=max(0.0, impact),
        liquidity_usd=quote.liquidity_hint,
        quote_fresh_ms=quote_fresh_ms,
        confidence=confidence,
        executable=True,
    )


def build_size_ladder(*, buy_quote, sell_quote, sizes: list[float], gas_cost_usd: float, flash_fee_bps: float, mempool_degradation_bps: float) -> tuple[SizeLadderPoint, ...]:
    ladder: list[SizeLadderPoint] = []
    leg1_tvl = max(float(buy_quote.liquidity_hint), 1.0)
    for amount in sizes:
        # Leg 1: quote token in → base token out at buy_quote pool.
        # buy_reserve_in  = quote token reserve (USDC side)   = liquidity / 2
        # buy_reserve_out = base token reserve (WMATIC/WBTC)  = (liquidity / 2) / price
        buy_reserve_in = max(buy_quote.liquidity_hint / 2.0, 1.0)
        buy_reserve_out = (
            buy_quote.liquidity_hint / 2.0 / buy_quote.price_quote_per_base
            if buy_quote.price_quote_per_base > 0 else 0.0
        )
        mid_out = _cpmm_out(amount, buy_reserve_in, buy_reserve_out, buy_quote.fee_bps)
        # Leg 2: base token in (mid_out) → quote token out at sell_quote pool.
        # sell_reserve_in  = base token reserve  = (liquidity / 2) / sell_price
        # sell_reserve_out = quote token reserve = liquidity / 2
        # Bug fix: previously sell_reserve_in used quote-token units; it must
        # use base-token units so _cpmm_out operates in the correct token space.
        sell_reserve_in = (
            sell_quote.liquidity_hint / 2.0 / sell_quote.price_quote_per_base
            if sell_quote.price_quote_per_base > 0 else 0.0
        )
        sell_reserve_out = max(sell_quote.liquidity_hint / 2.0, 1.0)
        final_out = _cpmm_out(mid_out, sell_reserve_in, sell_reserve_out, sell_quote.fee_bps)
        gross = final_out - amount
        costs = gas_cost_usd + amount * (flash_fee_bps / 10_000.0) + final_out * (mempool_degradation_bps / 10_000.0)
        net = gross - costs
        executable_bps = (gross / amount * 10_000.0) if amount > 0 else 0.0
        fraction = amount / leg1_tvl
        ladder.append(SizeLadderPoint(amount, mid_out, final_out, gross, costs, net, executable_bps, fraction, classify_flash_ladder_zone(fraction)))
    return tuple(ladder)


def build_market_distance_opportunity(*, base_symbol: str, quote_symbol: str = "USDCe", block_number: int = 0, sizes: list[float] | None = None, gas_cost_usd: float = 0.55, flash_fee_bps: float = 9.0, mempool_degradation_bps: float = 25.0, min_net_profit_usd: float = 0.0) -> MarketDistanceOpportunity | None:
    sizes = sizes or [100.0, 500.0, 1_000.0, 5_000.0]
    quotes = quotes_for_pair(base_symbol, quote_symbol)
    if len(quotes) < 2:
        return None
    best_buy = min(quotes, key=lambda q: q.price_quote_per_base)
    best_sell = max(quotes, key=lambda q: q.price_quote_per_base)
    if best_sell.pool == best_buy.pool or best_sell.price_quote_per_base <= best_buy.price_quote_per_base:
        return None
    raw_spread_bps = ((best_sell.price_quote_per_base - best_buy.price_quote_per_base) / best_buy.price_quote_per_base) * 10_000.0
    ladder = build_size_ladder(buy_quote=best_buy, sell_quote=best_sell, sizes=sizes, gas_cost_usd=gas_cost_usd, flash_fee_bps=flash_fee_bps, mempool_degradation_bps=mempool_degradation_bps)
    c1_eligible = [p for p in ladder if is_size_zone_allowed_for_c1(p.zone)]
    best_ladder = max(c1_eligible or list(ladder), key=lambda x: x.net_profit_usd) if ladder else None
    amount = best_ladder.amount_in_human if best_ladder else sizes[0]
    buy_out = best_ladder.buy_out if best_ladder else 0.0
    buy_point = _point_from_quote(quote=best_buy, token_in=quote_symbol, token_out=base_symbol, amount_in=amount, amount_out=buy_out, block_number=block_number)
    sell_point = _point_from_quote(quote=best_sell, token_in=base_symbol, token_out=quote_symbol, amount_in=buy_out, amount_out=best_ladder.sell_out if best_ladder else 0.0, block_number=block_number)
    net = best_ladder.net_profit_usd if best_ladder else float("-inf")
    c1_candidate = bool(best_ladder and is_size_zone_allowed_for_c1(best_ladder.zone) and net > min_net_profit_usd)
    reject_reason = None if c1_candidate else "net profit below threshold or selected size is probe-only"
    gas_adjusted = (net / amount * 10_000.0) if amount > 0 else 0.0
    return MarketDistanceOpportunity(
        id=_market_id(base_symbol, quote_symbol, best_buy.pool, best_sell.pool, block_number),
        chain_id=137,
        pair=f"{base_symbol}/{quote_symbol}",
        base_token=base_symbol,
        quote_token=quote_symbol,
        block_number=block_number,
        best_buy=buy_point,
        best_sell=sell_point,
        raw_spread_bps=raw_spread_bps,
        executable_spread_bps=best_ladder.executable_spread_bps if best_ladder else 0.0,
        gas_adjusted_spread_bps=gas_adjusted,
        size_ladder=ladder,
        c1_candidate=c1_candidate,
        c2_decision="DO_NOTHING",
        reject_reason=reject_reason,
    )


def market_opportunity_to_c1_packet(opportunity: MarketDistanceOpportunity) -> dict:
    eligible = [p for p in opportunity.size_ladder if is_size_zone_allowed_for_c1(p.zone)]
    selected = max(eligible or list(opportunity.size_ladder), key=lambda p: p.net_profit_usd)
    return {
        "source": "market_surface",
        "opportunity_id": opportunity.id,
        "chain_id": opportunity.chain_id,
        "pair": opportunity.pair,
        "base_token": opportunity.base_token,
        "quote_token": opportunity.quote_token,
        "block_number": opportunity.block_number,
        "candidate": opportunity.c1_candidate,
        "best_buy": opportunity.best_buy,
        "best_sell": opportunity.best_sell,
        "selected_size": selected,
        "size_ladder": opportunity.size_ladder,
        "reject_reason": opportunity.reject_reason,
        "authority": "scanner_snapshot_only_c1_must_recompute",
    }


def scan_market_surface(*, symbols: list[str] | None = None, quote_symbol: str = "USDCe", block_number: int = 0, top_n: int = 10) -> tuple[MarketDistanceOpportunity, ...]:
    symbols = symbols or [s for s in TOKENS.keys() if s != quote_symbol]
    opportunities = []
    for symbol in symbols:
        if symbol == quote_symbol:
            continue
        opp = build_market_distance_opportunity(base_symbol=symbol, quote_symbol=quote_symbol, block_number=block_number)
        if opp is not None:
            opportunities.append(opp)
    return tuple(sorted(opportunities, key=lambda o: max((p.net_profit_usd for p in o.size_ladder), default=-1e18), reverse=True)[:top_n])
