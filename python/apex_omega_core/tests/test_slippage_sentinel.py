import pytest
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.types import Slippage

def test_slippage_sentinel():
    sentinel = SlippageSentinel()
    protocol = sentinel.route({}, ['uniswap', 'sushiswap'])
    assert protocol == 'uniswap'

    slippage = sentinel.calculate_slippage(100.0, 101.0)
    assert slippage.difference == 1.0