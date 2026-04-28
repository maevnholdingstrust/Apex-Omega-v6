"""Tests for Uniswap V3 virtual-reserve math and C1/C2 V3 pool routing.

Covers:
- v3_virtual_reserves: correctness, edge cases, decimal handling
- v3_spot_price: correctness, edge cases
- C1AggressorApex._opportunity_to_route: V3 pool via sqrt_price_x96, via
  pre-populated reserves, via TVL fallback — no ValueError raised
- C2SurgeonApex._opportunity_to_route: same three paths
"""

from __future__ import annotations

import math
import pytest

from apex_omega_core.core.v3_math import v3_virtual_reserves, v3_spot_price
from apex_omega_core.core.types import ArbitrageOpportunity, Pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(
    address: str = "0xPoolAddr",
    dex: str = "uniswap",
    token0: str = "0xToken0",
    token1: str = "0xToken1",
    tvl_usd: float = 2_000_000.0,
    fee: float = 0.003,
    pool_type: str = "v3",
    reserve0: float = 0.0,
    reserve1: float = 0.0,
    sqrt_price_x96: float = 0.0,
    tick: int = 0,
    liquidity: float = 0.0,
    dec0: int = 18,
    dec1: int = 18,
) -> Pool:
    return Pool(
        address=address,
        dex=dex,
        token0=token0,
        token1=token1,
        tvl_usd=tvl_usd,
        fee=fee,
        pool_type=pool_type,
        reserve0=reserve0,
        reserve1=reserve1,
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
        liquidity=liquidity,
        dec0=dec0,
        dec1=dec1,
    )


def _make_opportunity(buy_pool: Pool, sell_pool: Pool) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        token="TOKEN",
        buy_pool=buy_pool,
        sell_pool=sell_pool,
        buy_price=1.0,
        sell_price=1.05,
        spread_bps=50.0,
        estimated_profit_usd=50.0,
        flash_loan_amount=50_000.0,
        flash_loan_token="USDC",
        path=[buy_pool.address, sell_pool.address],
        gas_estimate=0.25,
    )


# ---------------------------------------------------------------------------
# v3_virtual_reserves — math correctness
# ---------------------------------------------------------------------------

class TestV3VirtualReservesCorrectness:

    def test_balanced_pool_equal_reserves(self) -> None:
        """At sqrt(1) * 2^96 (price=1, 18/18 decimals) reserves are equal."""
        sqrt_p = 1.0  # price = 1 token1 per token0
        sqrt_price_x96 = sqrt_p * (2 ** 96)
        liquidity = 1e18

        r0, r1 = v3_virtual_reserves(sqrt_price_x96, liquidity, dec0=18, dec1=18)

        assert r0 == pytest.approx(r1, rel=1e-9)
        assert r0 == pytest.approx(1.0, rel=1e-9)  # L/sqrt_p / 10^18

    def test_price_ratio_matches_reserves_ratio(self) -> None:
        """reserve1/reserve0 equals the decimal-normalised price."""
        price = 2000.0  # e.g. WETH/USDC
        # USDC has 6 decimals (token1), WETH has 18 decimals (token0).
        # sqrtPriceX96 encodes sqrt(token1_raw / token0_raw), i.e. the raw ratio.
        # price_raw = decimal_price * 10^dec1 / 10^dec0
        #           = 2000 * 10^6 / 10^18 = 2e-12
        dec0, dec1 = 18, 6
        price_raw = price * (10 ** dec1) / (10 ** dec0)
        sqrt_price_x96 = math.sqrt(price_raw) * (2 ** 96)
        liquidity = 1e20

        r0, r1 = v3_virtual_reserves(sqrt_price_x96, liquidity, dec0=dec0, dec1=dec1)

        expected_price = v3_spot_price(sqrt_price_x96, dec0=dec0, dec1=dec1)
        assert expected_price == pytest.approx(price, rel=1e-6)
        assert r1 / r0 == pytest.approx(price, rel=1e-6)

    def test_reserves_positive_for_valid_inputs(self) -> None:
        sqrt_price_x96 = math.sqrt(1500.0) * (2 ** 96)
        liquidity = 5e21
        r0, r1 = v3_virtual_reserves(sqrt_price_x96, liquidity)
        assert r0 > 0
        assert r1 > 0

    def test_reserves_product_invariant(self) -> None:
        """L^2 == r0_raw * r1_raw (xy=k check in raw units)."""
        price = 1.0
        sqrt_p = math.sqrt(price)
        sqrt_price_x96 = sqrt_p * (2 ** 96)
        liquidity = 1e18

        # Raw virtual reserves (before decimal normalisation)
        r0_raw = liquidity / sqrt_p
        r1_raw = liquidity * sqrt_p
        product = r0_raw * r1_raw
        assert product == pytest.approx(liquidity ** 2, rel=1e-9)


class TestV3VirtualReservesEdgeCases:

    def test_zero_sqrt_price_returns_zero(self) -> None:
        r0, r1 = v3_virtual_reserves(0.0, 1e18)
        assert r0 == 0.0
        assert r1 == 0.0

    def test_zero_liquidity_returns_zero(self) -> None:
        r0, r1 = v3_virtual_reserves(math.sqrt(1.0) * (2 ** 96), 0.0)
        assert r0 == 0.0
        assert r1 == 0.0

    def test_negative_sqrt_price_returns_zero(self) -> None:
        r0, r1 = v3_virtual_reserves(-1.0, 1e18)
        assert r0 == 0.0
        assert r1 == 0.0

    def test_infinite_inputs_return_zero(self) -> None:
        r0, r1 = v3_virtual_reserves(math.inf, 1e18)
        assert r0 == 0.0 and r1 == 0.0

        r0, r1 = v3_virtual_reserves(math.sqrt(1.0) * (2 ** 96), math.inf)
        assert r0 == 0.0 and r1 == 0.0

    def test_nan_inputs_return_zero(self) -> None:
        r0, r1 = v3_virtual_reserves(math.nan, 1e18)
        assert r0 == 0.0 and r1 == 0.0

    def test_very_small_liquidity(self) -> None:
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        r0, r1 = v3_virtual_reserves(sqrt_price_x96, 1.0)
        # Should be positive and finite (not zero)
        assert r0 > 0 and r1 > 0

    def test_asymmetric_decimals(self) -> None:
        """USDC (6 dec) vs WETH (18 dec) — dec0=18, dec1=6."""
        # price_raw = 2000 * 10^6 / 10^18 = 2e-12
        dec0, dec1 = 18, 6
        price_raw = 2000.0 * (10 ** dec1) / (10 ** dec0)
        sqrt_price_x96 = math.sqrt(price_raw) * (2 ** 96)
        liquidity = 1e20

        r0, r1 = v3_virtual_reserves(sqrt_price_x96, liquidity, dec0=dec0, dec1=dec1)
        # Both must be positive
        assert r0 > 0 and r1 > 0


# ---------------------------------------------------------------------------
# v3_spot_price
# ---------------------------------------------------------------------------

class TestV3SpotPrice:

    def test_price_one(self) -> None:
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        price = v3_spot_price(sqrt_price_x96, dec0=18, dec1=18)
        assert price == pytest.approx(1.0, rel=1e-9)

    def test_price_2000(self) -> None:
        sqrt_price_x96 = math.sqrt(2000.0) * (2 ** 96)
        price = v3_spot_price(sqrt_price_x96, dec0=18, dec1=18)
        assert price == pytest.approx(2000.0, rel=1e-6)

    def test_zero_returns_zero(self) -> None:
        assert v3_spot_price(0.0) == 0.0

    def test_nan_returns_zero(self) -> None:
        assert v3_spot_price(math.nan) == 0.0

    def test_infinite_returns_zero(self) -> None:
        assert v3_spot_price(math.inf) == 0.0

    def test_asymmetric_decimals_weth_usdc(self) -> None:
        """Price: 2000 USDC per WETH where WETH=token0 (18 dec), USDC=token1 (6 dec).

        sqrtPriceX96 encodes sqrt(token1_raw / token0_raw).
        price_raw = 2000 * 10^6 / 10^18 = 2e-12
        """
        dec0, dec1 = 18, 6
        price_raw = 2000.0 * (10 ** dec1) / (10 ** dec0)
        sqrt_price_x96 = math.sqrt(price_raw) * (2 ** 96)
        price = v3_spot_price(sqrt_price_x96, dec0=dec0, dec1=dec1)
        assert price == pytest.approx(2000.0, rel=1e-6)


# ---------------------------------------------------------------------------
# C1AggressorApex — V3 pool routing (no ValueError)
# ---------------------------------------------------------------------------

class TestC1V3PoolRouting:
    """C1 must accept V3 pools and produce a valid 2-leg route."""

    def _run(self, buy_pool: Pool, sell_pool: Pool) -> list:
        from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
        c1 = C1AggressorApex()
        opp = _make_opportunity(buy_pool, sell_pool)
        return c1._opportunity_to_route(opp)

    # --- Path 1: sqrt_price_x96 + liquidity populated ---

    def test_v3_pool_via_sqrt_price_no_valueerror(self) -> None:
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        liquidity = 1e20
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=sqrt_price_x96,
            liquidity=liquidity,
        )
        route = self._run(pool, pool)
        assert isinstance(route, list)
        assert len(route) == 2

    def test_v3_pool_virtual_reserves_used(self) -> None:
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        liquidity = 1e20
        expected_r, _ = v3_virtual_reserves(sqrt_price_x96, liquidity)
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=sqrt_price_x96,
            liquidity=liquidity,
        )
        route = self._run(pool, pool)
        # buy leg: reserve_in = r1, reserve_out = r0
        assert route[0]["reserve_out"] == pytest.approx(expected_r, rel=1e-6)
        assert route[0]["reserve_in"] == pytest.approx(expected_r, rel=1e-6)

    # --- Path 2: pre-populated reserve0/reserve1 ---

    def test_v3_pool_via_prepopulated_reserves(self) -> None:
        pool = _make_pool(
            pool_type="v3",
            reserve0=500_000.0,
            reserve1=500_000.0,
        )
        route = self._run(pool, pool)
        assert route[0]["reserve_in"] == pytest.approx(500_000.0)
        assert route[0]["reserve_out"] == pytest.approx(500_000.0)

    # --- Path 3: TVL fallback (no V3 state, no reserves) ---

    def test_v3_pool_tvl_fallback_no_valueerror(self) -> None:
        pool = _make_pool(pool_type="v3", tvl_usd=1_000_000.0)
        route = self._run(pool, pool)
        assert isinstance(route, list)
        assert len(route) == 2
        assert route[0]["reserve_in"] == pytest.approx(1_000_000.0)

    # --- V3 pools produce a complete route dict ---

    def test_v3_route_has_required_keys(self) -> None:
        sqrt_price_x96 = math.sqrt(1.5) * (2 ** 96)
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=sqrt_price_x96,
            liquidity=1e21,
        )
        route = self._run(pool, pool)
        required = {"venue", "pair", "reserve_in", "reserve_out", "fee",
                    "price_in_usd", "price_out_usd", "tvl_usd",
                    "volume_24h_usd", "age_in_blocks"}
        for leg in route:
            assert required.issubset(leg.keys())

    # --- Mixed V2/V3 routes are accepted ---

    def test_mixed_v2_v3_route_no_valueerror(self) -> None:
        v2_pool = _make_pool(
            address="0xV2Pool",
            dex="quickswap",
            pool_type="v2",
            reserve0=1_000_000.0,
            reserve1=1_000_000.0,
        )
        v3_pool = _make_pool(
            address="0xV3Pool",
            dex="uniswap",
            pool_type="v3",
            sqrt_price_x96=math.sqrt(1.0) * (2 ** 96),
            liquidity=1e20,
        )
        opp = _make_opportunity(v2_pool, v3_pool)
        from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
        route = C1AggressorApex()._opportunity_to_route(opp)
        assert len(route) == 2


# ---------------------------------------------------------------------------
# C2SurgeonApex — V3 pool routing (no ValueError)
# ---------------------------------------------------------------------------

class TestC2V3PoolRouting:
    """C2 must accept V3 pools and produce a valid 2-leg route."""

    def _run(self, buy_pool: Pool, sell_pool: Pool) -> list:
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        c2 = C2SurgeonApex()
        opp = _make_opportunity(buy_pool, sell_pool)
        return c2._opportunity_to_route(opp)

    def test_v3_pool_via_sqrt_price_no_valueerror(self) -> None:
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=sqrt_price_x96,
            liquidity=1e20,
        )
        route = self._run(pool, pool)
        assert isinstance(route, list)
        assert len(route) == 2

    def test_v3_pool_via_prepopulated_reserves(self) -> None:
        pool = _make_pool(
            pool_type="v3",
            reserve0=750_000.0,
            reserve1=750_000.0,
        )
        route = self._run(pool, pool)
        assert route[0]["reserve_in"] == pytest.approx(750_000.0)

    def test_v3_pool_tvl_fallback_no_valueerror(self) -> None:
        pool = _make_pool(pool_type="v3", tvl_usd=2_000_000.0)
        route = self._run(pool, pool)
        assert isinstance(route, list)
        assert len(route) == 2

    def test_v3_virtual_reserves_buy_sell_ordering(self) -> None:
        """Buy leg uses (r1→r0), sell leg uses (r0→r1)."""
        sqrt_price_x96 = math.sqrt(1.0) * (2 ** 96)
        liquidity = 1e20
        r0, r1 = v3_virtual_reserves(sqrt_price_x96, liquidity)
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=sqrt_price_x96,
            liquidity=liquidity,
        )
        route = self._run(pool, pool)
        # buy leg: reserve_in=r1, reserve_out=r0
        assert route[0]["reserve_in"] == pytest.approx(r1, rel=1e-6)
        assert route[0]["reserve_out"] == pytest.approx(r0, rel=1e-6)
        # sell leg: reserve_in=r0, reserve_out=r1
        assert route[1]["reserve_in"] == pytest.approx(r0, rel=1e-6)
        assert route[1]["reserve_out"] == pytest.approx(r1, rel=1e-6)


# ---------------------------------------------------------------------------
# V2 pools are unaffected
# ---------------------------------------------------------------------------

class TestV2PoolsUnchanged:
    """Existing V2 pool behaviour must not regress."""

    def test_c1_v2_with_reserves(self) -> None:
        from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
        pool = _make_pool(
            pool_type="v2", dex="quickswap",
            reserve0=1_000_000.0, reserve1=1_020_000.0,
        )
        opp = _make_opportunity(pool, pool)
        route = C1AggressorApex()._opportunity_to_route(opp)
        assert route[0]["reserve_out"] == pytest.approx(1_000_000.0)
        assert route[0]["reserve_in"] == pytest.approx(1_020_000.0)

    def test_c2_v2_tvl_fallback(self) -> None:
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        pool = _make_pool(
            pool_type="v2", dex="sushiswap",
            tvl_usd=500_000.0,
        )
        opp = _make_opportunity(pool, pool)
        route = C2SurgeonApex()._opportunity_to_route(opp)
        assert isinstance(route, list)
        assert len(route) == 2


# ---------------------------------------------------------------------------
# Pool dataclass V3 fields
# ---------------------------------------------------------------------------

class TestPoolDataclassV3Fields:

    def test_pool_default_v3_fields_zero(self) -> None:
        pool = Pool(
            address="0xAddr",
            dex="uniswap",
            token0="0xT0",
            token1="0xT1",
            tvl_usd=1_000_000.0,
            fee=0.003,
        )
        assert pool.sqrt_price_x96 == 0.0
        assert pool.tick == 0
        assert pool.liquidity == 0.0
        assert pool.dec0 == 18
        assert pool.dec1 == 18

    def test_pool_accepts_v3_fields(self) -> None:
        pool = _make_pool(
            pool_type="v3",
            sqrt_price_x96=math.sqrt(2000.0) * (2 ** 96),
            tick=200_000,
            liquidity=1e21,
            dec0=18,
            dec1=6,
        )
        assert pool.sqrt_price_x96 > 0
        assert pool.tick == 200_000
        assert pool.liquidity == 1e21
        assert pool.dec0 == 18
        assert pool.dec1 == 6
