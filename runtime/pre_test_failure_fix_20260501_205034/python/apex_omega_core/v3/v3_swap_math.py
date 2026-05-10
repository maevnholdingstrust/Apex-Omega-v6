from decimal import Decimal
from .v3_pool_state import V3PoolState, validate_v3_state, price_token1_per_token0


def quote_v3_spot_exact_in(state: V3PoolState, amount_in: float, zero_for_one: bool) -> float:
    """
    Conservative spot quote only.
    This is NOT full initialized tick traversal.
    Used for gating/validation, not final execution unless external quoter/fork confirms.
    """
    if not validate_v3_state(state):
        raise ValueError("invalid V3 pool state")

    amount = Decimal(str(amount_in))
    fee_multiplier = Decimal(1) - (Decimal(state.fee_bps) / Decimal(10_000))
    price = price_token1_per_token0(state)

    if zero_for_one:
        return float(amount * fee_multiplier * price)

    return float((amount * fee_multiplier) / price)
