"""Tests for _compute_cpmm_arb_signals(): TVL floor, 10% flash sizing, net profit."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from apex_omega_core.core.live_data_feeds import (
    ArbitrageSignal,
    LiveDataFeeds,
    PoolReserveSnapshot,
    _AAVE_FLASH_FEE_RATE,
    _CPMM_FLASH_SIZE_FRACTION,
    _MIN_POOL_TVL_USD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool(
    pool_id: str,
    sym0: str,
    sym1: str,
    tvl_usd: float,
    token0_price: float,
    fee_tier: int = 3_000,
) -> PoolReserveSnapshot:
    """Construct a minimal PoolReserveSnapshot for testing."""
    return PoolReserveSnapshot(
        pool_id=pool_id,
        sym0=sym0,
        sym1=sym1,
        fee_tier=fee_tier,
        tvl_usd=tvl_usd,
        token0_price=token0_price,
        token1_price=1.0 / token0_price if token0_price else 0.0,
    )


def _feeds() -> LiveDataFeeds:
    """Return a LiveDataFeeds instance without triggering any network calls."""
    return LiveDataFeeds.__new__(LiveDataFeeds)


def _compute(
    pools: List[PoolReserveSnapshot],
    token_prices: Dict[str, float] | None = None,
) -> List[ArbitrageSignal]:
    return _feeds()._compute_cpmm_arb_signals(
        pools,
        token_prices or {"SYM0": 1.0, "SYM1": 1.0},
    )


# ---------------------------------------------------------------------------
# TVL floor: pools below $1,000 must be excluded
# ---------------------------------------------------------------------------

class TestTVLFloor:
    def test_both_pools_above_floor_produces_signal(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=2_000.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=3_000.0, token0_price=1.05),
        ]
        signals = _compute(pools)
        assert len(signals) == 1

    def test_one_pool_below_floor_produces_no_signal(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=500.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=3_000.0, token0_price=1.05),
        ]
        signals = _compute(pools)
        assert signals == []

    def test_both_pools_below_floor_produces_no_signal(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=100.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=200.0, token0_price=1.05),
        ]
        signals = _compute(pools)
        assert signals == []

    def test_pool_exactly_at_floor_produces_signal(self) -> None:
        """A pool exactly at the $1,000 floor should be included (≥ not >)."""
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=_MIN_POOL_TVL_USD, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=5_000.0, token0_price=1.05),
        ]
        signals = _compute(pools)
        assert len(signals) == 1

    def test_pool_just_below_floor_excluded(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=_MIN_POOL_TVL_USD - 0.01, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=5_000.0, token0_price=1.05),
        ]
        signals = _compute(pools)
        assert signals == []


# ---------------------------------------------------------------------------
# Flash size = 10% of min(TVL_pool1, TVL_pool2)
# ---------------------------------------------------------------------------

class TestFlashSizeFormula:
    def test_flash_size_is_ten_percent_of_min_tvl(self) -> None:
        tvl_a = 2_000.0
        tvl_b = 8_000.0
        expected_flash = min(tvl_a, tvl_b) * _CPMM_FLASH_SIZE_FRACTION  # 200.0

        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=tvl_a, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=tvl_b, token0_price=1.05),
        ]
        signal = _compute(pools)[0]
        assert signal.flash_size_usd == pytest.approx(expected_flash, rel=1e-6)

    def test_flash_size_uses_shallower_pool(self) -> None:
        """Verify the *minimum* TVL pool determines the flash size, not the larger one."""
        tvl_small = 5_000.0
        tvl_large = 500_000.0
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=tvl_small, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=tvl_large, token0_price=1.10),
        ]
        signal = _compute(pools)[0]
        assert signal.flash_size_usd == pytest.approx(tvl_small * 0.10, rel=1e-6)


# ---------------------------------------------------------------------------
# Net profit = gross profit – 5 bps Aave fee
# ---------------------------------------------------------------------------

class TestNetProfitAfterAaveFee:
    def test_net_profit_deducts_aave_fee(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=10_000.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=20_000.0, token0_price=1.05),
        ]
        signal = _compute(pools)[0]
        expected_fee = signal.flash_size_usd * _AAVE_FLASH_FEE_RATE
        assert signal.net_profit_usd == pytest.approx(
            signal.cpmm_arb_profit_usd - expected_fee, rel=1e-5
        )

    def test_aave_fee_rate_is_five_bps(self) -> None:
        """The fee rate constant must equal 5 bps = 0.0005."""
        assert _AAVE_FLASH_FEE_RATE == pytest.approx(0.0005)

    def test_net_profit_present_on_signal(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=5_000.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=5_000.0, token0_price=1.02),
        ]
        signal = _compute(pools)[0]
        assert hasattr(signal, "net_profit_usd")
        assert isinstance(signal.net_profit_usd, float)


# ---------------------------------------------------------------------------
# Signal completeness
# ---------------------------------------------------------------------------

class TestSignalFields:
    def test_all_new_fields_present(self) -> None:
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=50_000.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=40_000.0, token0_price=1.08),
        ]
        signal = _compute(pools)[0]
        assert hasattr(signal, "flash_size_usd")
        assert hasattr(signal, "cpmm_arb_profit_usd")
        assert hasattr(signal, "net_profit_usd")
        assert signal.flash_size_usd > 0.0

    def test_low_spread_pair_still_excluded(self) -> None:
        """Pair with < 1 bps spread must still produce no signal."""
        pools = [
            _pool("A", "SYM0", "SYM1", tvl_usd=50_000.0, token0_price=1.0),
            _pool("B", "SYM0", "SYM1", tvl_usd=50_000.0, token0_price=1.00001),
        ]
        signals = _compute(pools)
        assert signals == []
