import math
import pytest
from apex_omega_core.core.spread_alignment import (
    align_spread,
    bps_to_decimal,
    decimal_to_bps,
    compute_raw_spread,
    compute_raw_spread_bps,
)
from apex_omega_core.core.domain_types import Spread

def test_bps_conversion():
    assert bps_to_decimal(100) == 0.01
    assert decimal_to_bps(0.01) == 100

def test_align_spread():
    spread = Spread(symbol='TEST', bid=1.0, ask=1.01, timestamp=1234567890.0)
    aligned = align_spread(spread)
    assert aligned.symbol == 'TEST'
    assert aligned.bid == 1.0
    assert aligned.ask == 1.01

def test_align_spread_equal_bid_ask():
    spread = Spread(symbol='X', bid=2.0, ask=2.0, timestamp=0.0)
    aligned = align_spread(spread)
    assert aligned.bid == 2.0
    assert aligned.ask == 2.0

def test_align_spread_non_positive_prices_raise():
    with pytest.raises(ValueError, match="strictly positive"):
        align_spread(Spread(symbol='X', bid=0.0, ask=1.0, timestamp=0.0))
    with pytest.raises(ValueError, match="strictly positive"):
        align_spread(Spread(symbol='X', bid=-1.0, ask=1.0, timestamp=0.0))
    with pytest.raises(ValueError, match="strictly positive"):
        align_spread(Spread(symbol='X', bid=1.0, ask=0.0, timestamp=0.0))

def test_align_spread_inverted_raises():
    with pytest.raises(ValueError, match="inverted spread"):
        align_spread(Spread(symbol='X', bid=1.01, ask=1.0, timestamp=0.0))

def test_align_spread_non_finite_raises():
    with pytest.raises(ValueError, match="non-finite"):
        align_spread(Spread(symbol='X', bid=math.nan, ask=1.0, timestamp=0.0))
    with pytest.raises(ValueError, match="non-finite"):
        align_spread(Spread(symbol='X', bid=1.0, ask=math.inf, timestamp=0.0))

def test_compute_raw_spread_positive():
    # raw_spread = best_sell_price - best_buy_price
    assert compute_raw_spread(106.0, 100.0) == pytest.approx(6.0)

def test_compute_raw_spread_negative():
    # When sell < buy, spread is negative (no opportunity)
    assert compute_raw_spread(99.0, 100.0) == pytest.approx(-1.0)

def test_compute_raw_spread_zero():
    assert compute_raw_spread(100.0, 100.0) == pytest.approx(0.0)

def test_compute_raw_spread_bps_positive():
    # (106 - 100) / 100 * 10_000 = 600 bps
    assert compute_raw_spread_bps(106.0, 100.0) == pytest.approx(600.0)

def test_compute_raw_spread_bps_non_positive_buy_price_raises():
    with pytest.raises(ValueError):
        compute_raw_spread_bps(106.0, 0.0)
    with pytest.raises(ValueError):
        compute_raw_spread_bps(106.0, -1.0)

def test_compute_raw_spread_sign_convention():
    # spread = sell - buy (NOT buy - sell)
    best_sell_price = 2504.21
    best_buy_price = 2498.90
    raw = compute_raw_spread(best_sell_price, best_buy_price)
    assert raw > 0
    assert raw == pytest.approx(best_sell_price - best_buy_price)
    raw_bps = compute_raw_spread_bps(best_sell_price, best_buy_price)
    assert raw_bps == pytest.approx((raw / best_buy_price) * 10_000)