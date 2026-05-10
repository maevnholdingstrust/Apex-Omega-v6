import pytest

from apex_omega_core.core.polygon_arbitrage import ArbitrageDetector, PolygonDEXMonitor
from apex_omega_core.core.types import FlashLoanConfig, Pool


def _detector() -> ArbitrageDetector:
    return ArbitrageDetector(PolygonDEXMonitor(), FlashLoanConfig())


def test_compute_spread_bps_requires_buy_and_sell_prices() -> None:
    detector = _detector()

    assert detector._compute_spread_bps(250.45, 254.32) == pytest.approx(154.52186065082853)
    assert detector._compute_spread_bps(0.0, 254.32) is None
    assert detector._compute_spread_bps(250.45, 0.0) is None
    assert detector._compute_spread_bps(250.45, 250.45) is None
    assert detector._compute_spread_bps(254.32, 250.45) is None


def test_select_entry_exit_distinct_pools() -> None:
    detector = _detector()

    pool_a = Pool("0xaaa", "uniswap", "0xtoken", "0xusdc", 1_000_000.0, 0.003)
    pool_b = Pool("0xbbb", "quickswap", "0xtoken", "0xusdc", 1_000_000.0, 0.0025)

    buy_quotes = [(pool_a, 250.10), (pool_b, 250.40)]
    sell_quotes = [(pool_a, 251.20), (pool_b, 251.00)]

    selected = detector._select_entry_exit_pools(buy_quotes, sell_quotes)
    assert selected is not None

    buy_pool, buy_price, sell_pool, sell_price = selected
    assert buy_pool.address == "0xaaa"
    assert buy_price == 250.10
    assert sell_pool.address == "0xbbb"
    assert sell_price == 251.00
