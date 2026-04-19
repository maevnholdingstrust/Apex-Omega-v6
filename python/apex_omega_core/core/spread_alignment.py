from .types import Spread

def bps_to_decimal(bps: int) -> float:
    """Convert basis points to decimal."""
    return bps / 10000.0

def decimal_to_bps(decimal: float) -> int:
    """Convert decimal to basis points."""
    return int(decimal * 10000)

def compute_raw_spread(best_sell_price: float, best_buy_price: float) -> float:
    """Compute raw spread as the venue gap between the best sell and buy prices.

    Raw spread is the clean market gap between venues and is always defined as::

        raw_spread = best_sell_price - best_buy_price

    A positive value means the sell venue offers a higher price than the buy
    venue — a potential arbitrage opportunity exists.  Spread intentionally
    excludes fees, slippage, gas, and flash-loan costs; those are deducted
    separately when computing net edge.
    """
    return best_sell_price - best_buy_price

def compute_raw_spread_bps(best_sell_price: float, best_buy_price: float) -> float:
    """Compute raw spread in basis points.

    Expresses the venue gap relative to the buy price::

        raw_spread_bps = (best_sell_price - best_buy_price) / best_buy_price * 10_000

    Raises ``ValueError`` when ``best_buy_price`` is non-positive.
    """
    if best_buy_price <= 0:
        raise ValueError(f"best_buy_price must be positive, got {best_buy_price}")
    return ((best_sell_price - best_buy_price) / best_buy_price) * 10_000.0

def align_spread(spread: Spread) -> Spread:
    """BPS-native canonical layer for spread alignment."""
    # Canonical alignment logic
    aligned_bid = decimal_to_bps(spread.bid) / 10000.0
    aligned_ask = decimal_to_bps(spread.ask) / 10000.0
    return Spread(
        symbol=spread.symbol,
        bid=aligned_bid,
        ask=aligned_ask,
        timestamp=spread.timestamp
    )