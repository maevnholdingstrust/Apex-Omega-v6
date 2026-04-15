import pytest
from apex_omega_core.core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from apex_omega_core.core.types import Spread

def test_bps_conversion():
    assert bps_to_decimal(100) == 0.01
    assert decimal_to_bps(0.01) == 100

def test_align_spread():
    spread = Spread(symbol='TEST', bid=1.0, ask=1.01, timestamp=1234567890.0)
    aligned = align_spread(spread)
    assert aligned.symbol == 'TEST'
    assert aligned.bid == 1.0
    assert aligned.ask == 1.01