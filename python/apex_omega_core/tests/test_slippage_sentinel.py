import pytest
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.types import Slippage


def test_slippage_sentinel():
    sentinel = SlippageSentinel()
    protocol = sentinel.route({}, ['uniswap', 'sushiswap'])
    assert protocol == 'uniswap'

    slippage = sentinel.calculate_slippage(100.0, 101.0)
    assert slippage.difference == 1.0


def test_liquidity_metrics_gate_shallow_pools() -> None:
    sentinel = SlippageSentinel()

    depth = sentinel.depth_score(1_000.0, 1_200.0, 30.0, 0.4)
    health = sentinel.pool_health_index(
        depth_score=depth,
        volume_24h_usd=2_500_000.0,
        tvl_usd=900_000.0,
        age_in_blocks=1200,
    )

    assert depth > 500.0
    assert health > 0.75
    assert sentinel.depth_score(5.0, 5.0, 30.0, 10.0) == 0.0


def test_optimal_loan_and_path_liquidity_factor() -> None:
    sentinel = SlippageSentinel()

    optimal = sentinel.optimal_loan_amount(
        reserve_in=1_000_000.0,
        reserve_out=1_050_000.0,
        fee_bps=30.0,
        depth_score_value=1800.0,
        base_fee_gwei=80.0,
    )
    path_factor = sentinel.path_liquidity_factor([1800.0, 1600.0, 1400.0])

    assert optimal > 0.0
    assert 0.0 < path_factor <= 1.0


def test_optimize_returns_liquidity_adjusted_profit() -> None:
    sentinel = SlippageSentinel()
    route = [
        {
            'venue': 'uniswap',
            'pair': 'USDC → TOKEN',
            'reserve_in': 2_000_000.0,
            'reserve_out': 2_040_000.0,
            'fee': 0.003,
            'volume_24h_usd': 5_000_000.0,
            'tvl_usd': 1_500_000.0,
            'age_in_blocks': 100,
        },
        {
            'venue': 'quickswap',
            'pair': 'TOKEN → USDC',
            'reserve_in': 2_040_000.0,
            'reserve_out': 2_120_000.0,
            'fee': 0.0025,
            'volume_24h_usd': 6_000_000.0,
            'tvl_usd': 1_650_000.0,
            'age_in_blocks': 80,
        },
    ]

    result = sentinel.optimize(route, min_input=1_000.0, max_input=10_000.0, steps=8, raw_spread=25.0)

    assert 'raw_profit' in result
    assert 'path_liquidity_factor' in result
    assert 'total_cost_usd' in result
    assert 'net_profit_usd' in result
    assert result['profit'] == result['net_profit_usd']
    assert result['net_profit_usd'] <= result['raw_profit']


def test_simulate_route_tracks_usd_deductions_per_leg() -> None:
    sentinel = SlippageSentinel()
    route = [
        {
            'venue': 'storeA',
            'pair': 'USDC → TOKENA',
            'reserve_in': 100_000.0,
            'reserve_out': 100_000.0,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 200_000.0,
            'volume_24h_usd': 500_000.0,
            'age_in_blocks': 50,
        },
        {
            'venue': 'storeB',
            'pair': 'TOKENA → USDC',
            'reserve_in': 100_000.0,
            'reserve_out': 105_000.0,
            'fee': 0.003,
            'price_in_usd': 1.05,
            'price_out_usd': 1.0,
            'tvl_usd': 205_000.0,
            'volume_24h_usd': 450_000.0,
            'age_in_blocks': 50,
        },
    ]

    result = sentinel.optimize(route, min_input=5_000.0, max_input=5_000.0, steps=2, raw_spread=250.0)

    assert len(result['slippage_per_leg']) == 2
    assert result['raw_profit'] > 0
    assert result['total_cost_usd'] >= 0
    assert result['net_profit_usd'] == pytest.approx(result['raw_profit'] - result['total_cost_usd'])