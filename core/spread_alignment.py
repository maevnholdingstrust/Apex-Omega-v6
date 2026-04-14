from .types import Spread

def bps_to_decimal(bps: int) -> float:
    """Convert basis points to decimal."""
    return bps / 10000.0

def decimal_to_bps(decimal: float) -> int:
    """Convert decimal to basis points."""
    return int(decimal * 10000)

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