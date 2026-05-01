from .domain_types import Spread

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
    excludes DEX fees, slippage, gas, and flash-loan costs. DEX fees belong
    inside AMM outputs, flash fees belong at route-token profit, and gas
    belongs at owner submission/ranking.
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
    """BPS-native canonical layer for spread alignment.

    Validates that the spread is well-formed and returns it unchanged when
    valid.  Raises ``ValueError`` for any of the following conditions:

    * ``bid`` or ``ask`` is non-positive
    * ``bid`` is greater than ``ask`` (inverted spread)
    * Either price is not finite (``NaN`` or ``inf``)

    The function intentionally does not mutate prices because any transformation
    here would create a silent divergence between the validated spread and the
    values passed downstream.  Callers that need unit conversion should use
    :func:`bps_to_decimal` / :func:`decimal_to_bps` directly.
    """
    import math as _math
    if not _math.isfinite(spread.bid) or not _math.isfinite(spread.ask):
        raise ValueError(
            f"align_spread: non-finite price(s) — bid={spread.bid}, ask={spread.ask}"
        )
    if spread.bid <= 0 or spread.ask <= 0:
        raise ValueError(
            f"align_spread: prices must be strictly positive — bid={spread.bid}, ask={spread.ask}"
        )
    if spread.bid > spread.ask:
        raise ValueError(
            f"align_spread: inverted spread — bid={spread.bid} > ask={spread.ask}"
        )
    return Spread(
        symbol=spread.symbol,
        bid=spread.bid,
        ask=spread.ask,
        timestamp=spread.timestamp,
    )
