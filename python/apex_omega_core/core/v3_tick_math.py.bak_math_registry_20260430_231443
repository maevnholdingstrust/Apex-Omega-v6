from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext

getcontext().prec = 80
Q96 = Decimal(2) ** 96


@dataclass(frozen=True)
class TickLiquidityNet:
    tick: int
    liquidity_net: int


@dataclass(frozen=True)
class V3PoolState:
    sqrt_price_x96: int
    current_tick: int
    liquidity: int
    fee_pips: int
    tick_spacing: int
    initialized_ticks: tuple[TickLiquidityNet, ...]


@dataclass(frozen=True)
class V3SwapResult:
    amount_in: float
    amount_out: float
    final_sqrt_price_x96: int
    final_tick: int
    crossed_ticks: int
    exact: bool
    reason: str


def tick_to_sqrt_price_x96(tick: int) -> int:
    sqrt_price = Decimal("1.0001") ** (Decimal(tick) / Decimal(2))
    return int(sqrt_price * Q96)


def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    sqrt_p = Decimal(sqrt_price_x96) / Q96
    raw = sqrt_p * sqrt_p
    return float(raw * (Decimal(10) ** Decimal(decimals0 - decimals1)))


def _amount0_delta(liquidity: Decimal, sqrt_a: Decimal, sqrt_b: Decimal) -> Decimal:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return liquidity * (sqrt_b - sqrt_a) / (sqrt_b * sqrt_a)


def _amount1_delta(liquidity: Decimal, sqrt_a: Decimal, sqrt_b: Decimal) -> Decimal:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return liquidity * (sqrt_b - sqrt_a)


def simulate_exact_input_tick_walk(
    state: V3PoolState,
    amount_in: float,
    zero_for_one: bool,
    decimals_in: int,
    decimals_out: int,
) -> V3SwapResult:
    """Tick-walk scaffold for Uniswap V3 exactInput swaps.

    This is intentionally fail-closed for execution unless initialized tick data is
    supplied. It can exactly traverse supplied initialized ticks; without tick data,
    callers must not mark the result execution-grade.
    """
    if amount_in <= 0:
        return V3SwapResult(amount_in, 0.0, state.sqrt_price_x96, state.current_tick, 0, False, "amount_in <= 0")
    if state.liquidity <= 0 or state.sqrt_price_x96 <= 0:
        return V3SwapResult(amount_in, 0.0, state.sqrt_price_x96, state.current_tick, 0, False, "invalid liquidity/price")
    if not state.initialized_ticks:
        return V3SwapResult(amount_in, 0.0, state.sqrt_price_x96, state.current_tick, 0, False, "missing initialized tick table")

    remaining = Decimal(amount_in) * (Decimal(10) ** decimals_in)
    fee_factor = Decimal(1) - (Decimal(state.fee_pips) / Decimal(1_000_000))
    sqrt_p = Decimal(state.sqrt_price_x96) / Q96
    liquidity = Decimal(state.liquidity)
    current_tick = state.current_tick
    ticks = sorted(state.initialized_ticks, key=lambda t: t.tick, reverse=zero_for_one)
    crossed = 0
    out_raw = Decimal(0)

    for tick in ticks:
        if zero_for_one and tick.tick >= current_tick:
            continue
        if not zero_for_one and tick.tick <= current_tick:
            continue
        target_sqrt = Decimal(tick_to_sqrt_price_x96(tick.tick)) / Q96
        usable_in = remaining * fee_factor
        needed = _amount0_delta(liquidity, target_sqrt, sqrt_p) if zero_for_one else _amount1_delta(liquidity, sqrt_p, target_sqrt)
        if usable_in >= needed:
            if zero_for_one:
                out_raw += _amount1_delta(liquidity, target_sqrt, sqrt_p)
            else:
                out_raw += _amount0_delta(liquidity, sqrt_p, target_sqrt)
            remaining -= needed / fee_factor
            sqrt_p = target_sqrt
            liquidity = liquidity - Decimal(tick.liquidity_net) if zero_for_one else liquidity + Decimal(tick.liquidity_net)
            current_tick = tick.tick
            crossed += 1
            if remaining <= 0 or liquidity <= 0:
                break
        else:
            # Partial within current tick range. Conservative approximation within active liquidity.
            if zero_for_one:
                new_sqrt = (liquidity * sqrt_p) / (liquidity + usable_in * sqrt_p)
                out_raw += _amount1_delta(liquidity, new_sqrt, sqrt_p)
            else:
                new_sqrt = sqrt_p + usable_in / liquidity
                out_raw += _amount0_delta(liquidity, sqrt_p, new_sqrt)
            sqrt_p = new_sqrt
            remaining = Decimal(0)
            break

    exact = remaining <= Decimal("0.0000001")
    return V3SwapResult(
        amount_in=amount_in,
        amount_out=float(out_raw / (Decimal(10) ** decimals_out)),
        final_sqrt_price_x96=int(sqrt_p * Q96),
        final_tick=current_tick,
        crossed_ticks=crossed,
        exact=exact,
        reason="tick-walk exact" if exact else "insufficient initialized tick range",
    )
