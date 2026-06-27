import pytest

from apex_omega_core.core.domain_types import FlashLoanConfig, Pool
from apex_omega_core.core.onchain_v2_discovery import OnchainV2Pool
from apex_omega_core.core.polygon_arbitrage import ArbitrageDetector, PolygonDEXMonitor


def _pool(tvl_usd: float, *, tvl_verified: bool = True) -> Pool:
    pool = Pool(
        address="0x" + "1" * 40,
        dex="quickswap",
        token0="0x" + "2" * 40,
        token1="0x" + "3" * 40,
        tvl_usd=tvl_usd,
        fee=0.003,
    )
    pool.tvl_verified = tvl_verified
    return pool


def test_pool_from_onchain_v2_computes_tvl_from_stable_reserve():
    monitor = PolygonDEXMonitor()
    raw = OnchainV2Pool(
        dex_name="quickswap",
        factory="0x" + "4" * 40,
        pair_address="0x" + "5" * 40,
        token0="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        token1="0x" + "6" * 40,
        reserve0=1_000_000 * 10**6,
        reserve1=100 * 10**18,
    )

    pool = monitor._pool_from_onchain_v2(raw)

    assert pool.tvl_usd == pytest.approx(2_000_000.0)
    assert pool.tvl_verified is False


def test_canonical_tokens_are_prioritized_before_live_inventory():
    monitor = PolygonDEXMonitor()
    arbitrary = "0x" + "9" * 40

    tokens = monitor._prioritize_canonical_tokens(
        {arbitrary: {"address": arbitrary, "symbol": "ARB", "tvl_usd": 0.0}}
    )

    first_symbols = [token["symbol"] for token in list(tokens.values())[:5]]
    assert first_symbols == ["USDC.e", "USDC", "USDT", "DAI", "FRAX"]
    assert arbitrary in {address.lower() for address in tokens}


def test_seeded_canonical_tokens_are_scannable_without_registry_refresh():
    monitor = PolygonDEXMonitor()

    assert [token["symbol"] for token in monitor.get_tokens()[:5]] == [
        "USDC.e",
        "USDC",
        "USDT",
        "DAI",
        "FRAX",
    ]


def test_flash_loan_size_uses_weakest_pool_tvl():
    detector = ArbitrageDetector(
        PolygonDEXMonitor(),
        FlashLoanConfig(min_amount_usd=5_000.0, max_pool_tvl_percent=0.30),
    )

    assert detector._flash_loan_size_for_token([_pool(20_000.0), _pool(40_000.0)]) == 20_000.0


def test_flash_loan_size_returns_lowest_pool_tvl():
    detector = ArbitrageDetector(
        PolygonDEXMonitor(),
        FlashLoanConfig(min_amount_usd=5_000.0, max_pool_tvl_percent=0.30),
    )

    assert detector._flash_loan_size_for_token([_pool(100_000.0), _pool(200_000.0)]) == 100_000.0


def test_flash_loan_size_rejects_unverified_tvl():
    detector = ArbitrageDetector(
        PolygonDEXMonitor(),
        FlashLoanConfig(min_amount_usd=5_000.0, max_pool_tvl_percent=0.15),
    )

    assert detector._flash_loan_size_for_token(
        [_pool(100_000.0), _pool(100_000.0, tvl_verified=False)]
    ) == 0.0


def test_pool_from_onchain_v2_rejects_extreme_known_token_imbalance():
    monitor = PolygonDEXMonitor()
    raw = OnchainV2Pool(
        dex_name="quickswap",
        factory="0x" + "4" * 40,
        pair_address="0x" + "5" * 40,
        token0="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        token1="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        reserve0=1_000_000 * 10**6,
        reserve1=1,
    )

    pool = monitor._pool_from_onchain_v2(raw)

    assert pool.tvl_usd == 0.0
    assert pool.tvl_verified is False


@pytest.mark.asyncio
async def test_find_opportunities_reuses_supplied_pool_snapshot(monkeypatch):
    monitor = PolygonDEXMonitor()
    detector = ArbitrageDetector(monitor, FlashLoanConfig())

    async def _unexpected_scan(_tokens):
        raise AssertionError("pool discovery should not run twice")

    monkeypatch.setattr(monitor, "scan_all_dexes", _unexpected_scan)

    assert await detector.find_opportunities([], pools=[]) == []


@pytest.mark.asyncio
async def test_effective_price_uses_observed_cpmm_reserves():
    monitor = PolygonDEXMonitor()
    detector = ArbitrageDetector(monitor, FlashLoanConfig())
    raw = OnchainV2Pool(
        dex_name="quickswap",
        factory="0x" + "4" * 40,
        pair_address="0x" + "5" * 40,
        token0="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        token1="0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        reserve0=1_000_000 * 10**6,
        reserve1=1_000_000 * 10**6,
    )
    pool = monitor._pool_from_onchain_v2(raw)

    buy_price = await detector._get_effective_price(pool, raw.token0, 10_000.0, "buy")
    sell_price = await detector._get_effective_price(pool, raw.token0, 10_000.0, "sell")

    assert buy_price > 1.0
    assert sell_price < 1.0
    assert buy_price > sell_price


@pytest.mark.asyncio
async def test_monitor_get_price_uses_cpmm_formula():
    monitor = PolygonDEXMonitor()
    pool = Pool(
        address="0x" + "1" * 40,
        dex="quickswap",
        token0="0x" + "2" * 40,
        token1="0x" + "3" * 40,
        tvl_usd=2_000.0,
        fee=0.003,
        reserve0=1_000.0,
        reserve1=1_000.0,
    )

    quoted = await monitor.get_price(pool, pool.token0, 100.0)
    expected = (100.0 * 0.997 * 1_000.0) / (1_000.0 + 100.0 * 0.997)

    assert quoted == pytest.approx(expected)
