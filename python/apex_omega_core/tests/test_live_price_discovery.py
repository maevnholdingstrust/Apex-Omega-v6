from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from dry_run import (  # noqa: E402
    _PoolSnapshot,
    _derive_token_prices_with_report,
    _filter_pool_universe_with_report,
)


def _pool(pair: str, dex: str, price: float, reserve0: float = 1_000_000.0, reserve1: float = 1_000_000.0) -> _PoolSnapshot:
    sym0, sym1 = pair.split("/")
    return _PoolSnapshot(
        pool_address=f"0x{dex}_{sym0}_{sym1}",
        dex=dex,
        fee=0.003,
        sym0=sym0,
        sym1=sym1,
        reserve0=reserve0,
        reserve1=reserve1,
        price=price,
    )


def test_price_discovery_derives_multi_hop_prices_from_live_pool_graph() -> None:
    pool_map = {
        "USDC/WMATIC": [_pool("USDC/WMATIC", "qsv2", 2.5)],
        "WMATIC/WETH": [_pool("WMATIC/WETH", "qsv2", 0.00016)],
    }

    prices, report = _derive_token_prices_with_report(pool_map)

    assert prices["USDC"] == 1.0
    assert round(prices["WMATIC"], 6) == 0.4
    assert round(prices["WETH"], 2) == 2500.0
    assert report.evidence["WETH"]["path"] == "USDC->WMATIC->WETH"
    assert report.unpriced_tokens == []


def test_stable_aliases_are_priced_without_hardcoded_volatile_fallbacks() -> None:
    pool_map = {
        "USDCe/FRAX": [_pool("USDCe/FRAX", "qsv2", 1.0)],
        "MAI/TUSD": [_pool("MAI/TUSD", "qsv2", 1.0)],
    }

    prices, report = _derive_token_prices_with_report(pool_map)

    for sym in ("USDCe", "FRAX", "MAI", "TUSD"):
        assert prices[sym] == 1.0
        assert sym in report.priced_tokens


def test_unpriced_token_quarantines_pool_instead_of_silent_drop() -> None:
    pool_map = {
        "ABC/XYZ": [
            _pool("ABC/XYZ", "qsv2", 2.0),
            _pool("ABC/XYZ", "univ3_3000", 2.1),
        ]
    }
    prices, price_report = _derive_token_prices_with_report(pool_map)

    cleaned, report = _filter_pool_universe_with_report(
        pool_map,
        prices,
        min_tvl_usd=100.0,
        price_report=price_report,
    )

    assert cleaned == {}
    assert report.unpriced_tokens == ["ABC", "XYZ"]
    assert report.quarantine_summary["missing_token_price"] == 2
    assert report.quarantine_summary["insufficient_usable_venues"] == 1
    assert report.quarantined_pools[0]["missing_tokens"] == ["ABC", "XYZ"]
