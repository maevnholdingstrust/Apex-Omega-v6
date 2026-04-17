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


# ---------------------------------------------------------------------------
# APEX-OMEGA v7 Capital Model tests
# ---------------------------------------------------------------------------

def test_best_entry_price_spot_limit() -> None:
    """As amount_base_in → 0, best_entry_price → spot = reserve_base / reserve_token (before fee)."""
    sentinel = SlippageSentinel()
    # Tiny buy: price should be very close to spot (reserve_base / reserve_token adjusted for fee)
    entry = sentinel.best_entry_price(1.0, 1_000_000.0, 1_000_000.0, 0.003)
    spot = 1_000_000.0 / 1_000_000.0  # = 1.0
    # With fee the effective entry is slightly above spot
    assert entry > spot
    assert entry == pytest.approx(spot, rel=0.01)


def test_best_exit_price_spot_limit() -> None:
    """As amount_token_in → 0, best_exit_price → spot = reserve_base * (1-fee) / reserve_token."""
    sentinel = SlippageSentinel()
    exit_price = sentinel.best_exit_price(1.0, 1_000_000.0, 1_000_000.0, 0.003)
    spot = 1_000_000.0 / 1_000_000.0  # = 1.0
    # With fee the effective exit is slightly below spot
    assert exit_price < spot
    assert exit_price == pytest.approx(spot, rel=0.01)


def test_best_entry_price_increases_with_size() -> None:
    """Larger trade → higher effective buy price (price impact)."""
    sentinel = SlippageSentinel()
    small = sentinel.best_entry_price(1_000.0, 1_000_000.0, 1_000_000.0, 0.003)
    large = sentinel.best_entry_price(100_000.0, 1_000_000.0, 1_000_000.0, 0.003)
    assert large > small


def test_best_exit_price_decreases_with_size() -> None:
    """Larger sell → lower effective exit price (price impact)."""
    sentinel = SlippageSentinel()
    small = sentinel.best_exit_price(1_000.0, 1_000_000.0, 1_000_000.0, 0.003)
    large = sentinel.best_exit_price(100_000.0, 1_000_000.0, 1_000_000.0, 0.003)
    assert large < small


def test_best_entry_price_degenerate_inputs() -> None:
    sentinel = SlippageSentinel()
    assert sentinel.best_entry_price(0.0, 1_000_000.0, 1_000_000.0, 0.003) == float('inf')
    assert sentinel.best_entry_price(-1.0, 1_000_000.0, 1_000_000.0, 0.003) == float('inf')
    assert sentinel.best_entry_price(1_000.0, 0.0, 1_000_000.0, 0.003) == float('inf')


def test_best_exit_price_degenerate_inputs() -> None:
    sentinel = SlippageSentinel()
    assert sentinel.best_exit_price(0.0, 1_000_000.0, 1_000_000.0, 0.003) == 0.0
    assert sentinel.best_exit_price(1_000.0, 0.0, 1_000_000.0, 0.003) == 0.0


def test_compute_net_edge_v7_positive_edge() -> None:
    """When sell > buy and costs are small, net_edge > 0 and should_execute is True."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.00,
        buy_slippage=0.002,
        sell_price=1.05,
        sell_slippage=0.002,
        ml_slippage=0.006,
        raw_spread=0.05,
        buffer_rate=0.1,
        trade_size=50_000.0,
        fees=0.001,
    )
    # money_out = 1.00 + 0.002 = 1.002
    # money_in  = 1.05 - 0.002 = 1.048
    # edge      = 1.048 - 1.002 = 0.046
    # adjusted_slippage = 0.006 / 3 = 0.002
    # ev_buffer = 0.05 * 0.1 * (50_000 / 100_000) = 0.0025
    # net_edge  = 0.046 - 0.002 - 0.0025 - 0.001 = 0.0405
    assert result['money_out'] == pytest.approx(1.002)
    assert result['money_in'] == pytest.approx(1.048)
    assert result['edge'] == pytest.approx(0.046)
    assert result['adjusted_slippage'] == pytest.approx(0.002)
    assert result['ev_buffer'] == pytest.approx(0.0025)
    assert result['net_edge'] == pytest.approx(0.046 - 0.002 - 0.0025 - 0.001)
    assert result['should_execute'] is True


def test_compute_net_edge_v7_negative_edge() -> None:
    """When costs exceed the spread, net_edge <= 0 and should_execute is False."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.00,
        buy_slippage=0.01,
        sell_price=1.01,
        sell_slippage=0.01,
        ml_slippage=0.03,
        raw_spread=0.01,
        buffer_rate=0.5,
        trade_size=200_000.0,
        fees=0.005,
    )
    assert result['net_edge'] <= 0.0
    assert result['should_execute'] is False


def test_compute_net_edge_v7_zero_spread() -> None:
    """No spread → edge is negative (buy_slippage + sell_slippage push it below zero)."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.0,
        buy_slippage=0.005,
        sell_price=1.0,
        sell_slippage=0.005,
        ml_slippage=0.0,
        raw_spread=0.0,
        buffer_rate=0.1,
        trade_size=10_000.0,
        fees=0.0,
    )
    assert result['edge'] == pytest.approx(-0.01)
    assert result['should_execute'] is False


def test_compute_net_edge_v7_adjusted_slippage_divisor() -> None:
    """adjusted_slippage is always ml_slippage / 3."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.0, buy_slippage=0.0,
        sell_price=2.0, sell_slippage=0.0,
        ml_slippage=0.9,
        raw_spread=0.0, buffer_rate=0.0, trade_size=0.0, fees=0.0,
    )
    assert result['adjusted_slippage'] == pytest.approx(0.3)
    assert result['net_edge'] == pytest.approx(1.0 - 0.3)


def test_compute_net_edge_v7_ev_buffer_scaling() -> None:
    """EV_buffer scales linearly with trade_size / 100_000."""
    sentinel = SlippageSentinel()
    r1 = sentinel.compute_net_edge_v7(
        buy_price=0.0, buy_slippage=0.0,
        sell_price=0.0, sell_slippage=0.0,
        ml_slippage=0.0, raw_spread=1.0, buffer_rate=0.2,
        trade_size=100_000.0, fees=0.0,
    )
    r2 = sentinel.compute_net_edge_v7(
        buy_price=0.0, buy_slippage=0.0,
        sell_price=0.0, sell_slippage=0.0,
        ml_slippage=0.0, raw_spread=1.0, buffer_rate=0.2,
        trade_size=200_000.0, fees=0.0,
    )
    # EV_buffer(200k) should be double EV_buffer(100k)
    assert r2['ev_buffer'] == pytest.approx(r1['ev_buffer'] * 2.0)