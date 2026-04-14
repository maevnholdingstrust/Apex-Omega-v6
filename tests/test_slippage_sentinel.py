import pytest
from core.slippage_sentinel import SlippageSentinel
from core.types import Slippage

def test_slippage_sentinel():
    sentinel = SlippageSentinel()
    protocol = sentinel.route({}, ['http', 'tcp'])
    assert protocol == 'http'

    slippage = sentinel.calculate_slippage(100.0, 101.0)
    assert slippage.difference == 1.0