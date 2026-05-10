from dataclasses import dataclass
from decimal import Decimal, getcontext

getcontext().prec = 80

Q96 = Decimal(2) ** 96
Q192 = Decimal(2) ** 192


@dataclass(frozen=True)
class V3PoolState:
    token0: str
    token1: str
    fee_bps: int
    sqrt_price_x96: int
    liquidity: int
    tick: int
    tick_spacing: int
    decimals0: int
    decimals1: int
    pool_address: str
    dex: str = "UNISWAP_V3"


def price_token1_per_token0(state: V3PoolState) -> Decimal:
    sqrt_price = Decimal(state.sqrt_price_x96)
    raw_price = (sqrt_price * sqrt_price) / Q192
    decimal_adjustment = Decimal(10) ** Decimal(state.decimals0 - state.decimals1)
    return raw_price * decimal_adjustment


def validate_v3_state(state: V3PoolState) -> bool:
    if state.sqrt_price_x96 <= 0:
        return False
    if state.liquidity <= 0:
        return False
    if state.tick_spacing <= 0:
        return False
    if state.fee_bps < 0:
        return False
    if not state.token0 or not state.token1:
        return False
    return True
