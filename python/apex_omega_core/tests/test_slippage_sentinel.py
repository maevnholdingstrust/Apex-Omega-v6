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


# ---------------------------------------------------------------------------
# C1 → state mutation → C2 pipeline correctness
# ---------------------------------------------------------------------------

def test_apply_post_trade_state_mutates_reserves() -> None:
    """C2 must evaluate post-trade reserves, not the same state as C1.

    After C1 executes (Punch 1) with amount_in / amount_out per leg, the pool
    reserves must update:
      new_reserve_in  = reserve_in  + amount_in
      new_reserve_out = reserve_out - amount_out

    This test verifies that apply_post_trade_state returns a route whose
    reserves differ from the original, representing the mutated on-chain state.
    """
    sentinel = SlippageSentinel()

    route = [
        {
            'venue': 'uniswap',
            'pair': 'USDC → TOKEN',
            'reserve_in': 1_000_000.0,
            'reserve_out': 1_050_000.0,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 2_000_000.0,
            'volume_24h_usd': 5_000_000.0,
            'age_in_blocks': 100,
        },
        {
            'venue': 'quickswap',
            'pair': 'TOKEN → USDC',
            'reserve_in': 1_050_000.0,
            'reserve_out': 1_100_000.0,
            'fee': 0.0025,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 2_100_000.0,
            'volume_24h_usd': 4_500_000.0,
            'age_in_blocks': 80,
        },
    ]

    # Simulate C1 sentinel output: C1 traded 10_000 in on leg 0 and received 9_800 out,
    # then traded 9_800 in on leg 1 and received 9_500 out.
    c1_sentinel_output = {
        'optimal_input': 10_000.0,
        'final_output': 9_500.0,
        'slippage_per_leg': [
            {'amount_in': 10_000.0, 'amount_out': 9_800.0},
            {'amount_in': 9_800.0, 'amount_out': 9_500.0},
        ],
    }

    post_route, post_spread = sentinel.apply_post_trade_state(route, c1_sentinel_output)

    # Reserves must differ from pre-trade values.
    assert post_route[0]['reserve_in'] == pytest.approx(1_010_000.0)   # 1_000_000 + 10_000
    assert post_route[0]['reserve_out'] == pytest.approx(1_040_200.0)  # 1_050_000 - 9_800
    assert post_route[1]['reserve_in'] == pytest.approx(1_059_800.0)   # 1_050_000 + 9_800
    assert post_route[1]['reserve_out'] == pytest.approx(1_090_500.0)  # 1_100_000 - 9_500

    # post_route is a new list; original route must be untouched.
    assert route[0]['reserve_in'] == 1_000_000.0
    assert route[0]['reserve_out'] == 1_050_000.0
    assert route[1]['reserve_in'] == 1_050_000.0
    assert route[1]['reserve_out'] == 1_100_000.0

    # post_spread is derived from updated reserve ratios; it may be a different
    # value (likely smaller, since C1 consumed part of the edge).
    assert isinstance(post_spread, float)


def test_apply_post_trade_state_spread_compresses_after_c1() -> None:
    """C1 consuming the spread must reduce the observed edge available to C2.

    Starting from a route with a noticeable imbalance (sell pool richer than
    buy pool), C1's trade tightens both pools toward equilibrium.  The resulting
    post-trade spread must be smaller (or zero/negative) compared to the
    original raw_spread computed from the same route.
    """
    sentinel = SlippageSentinel()

    # buy_pool: 1 USDC buys 1.05 TOKEN  → buy_price = 1/1.05 ≈ 0.952
    # sell_pool: 1.10 TOKEN buys 1 USDC → sell_price = 1.10/1.00 = 1.10
    route = [
        {
            'venue': 'buy_dex',
            'pair': 'USDC → TOKEN',
            'reserve_in': 1_000_000.0,
            'reserve_out': 1_050_000.0,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 2_000_000.0,
            'volume_24h_usd': 5_000_000.0,
            'age_in_blocks': 100,
        },
        {
            'venue': 'sell_dex',
            'pair': 'TOKEN → USDC',
            'reserve_in': 1_000_000.0,
            'reserve_out': 1_100_000.0,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 2_100_000.0,
            'volume_24h_usd': 4_500_000.0,
            'age_in_blocks': 100,
        },
    ]

    # Pre-trade raw spread from reserve ratios.
    buy_price_pre = route[0]['reserve_in'] / route[0]['reserve_out']
    sell_price_pre = route[1]['reserve_out'] / route[1]['reserve_in']
    raw_spread_pre = sell_price_pre - buy_price_pre
    assert raw_spread_pre > 0, "test setup requires a positive pre-trade spread"

    # C1 buys TOKEN on leg 0 (sends USDC in, receives TOKEN out) then sells on leg 1.
    c1_sentinel_output = {
        'optimal_input': 50_000.0,
        'final_output': 53_000.0,
        'slippage_per_leg': [
            {'amount_in': 50_000.0, 'amount_out': 51_500.0},
            {'amount_in': 51_500.0, 'amount_out': 53_000.0},
        ],
    }

    _post_route, post_spread = sentinel.apply_post_trade_state(route, c1_sentinel_output)

    # After C1's trade the spread available to C2 must be smaller.
    assert post_spread < raw_spread_pre


def test_process_discovery_pipeline_c2_uses_post_trade_state() -> None:
    """C2 in process_discovery_pipeline must receive post-trade reserves, not pre-trade.

    The route passed to C2's decide_contract_action must have reserves that
    differ from the original route after C1 has executed.
    """
    import asyncio
    from unittest.mock import patch
    from apex_omega_core.strategies.execution_router import ExecutionRouter

    router = ExecutionRouter()

    route = [
        {
            'venue': 'uniswap',
            'pair': 'USDC → TOKEN',
            'reserve_in': 2_000_000.0,
            'reserve_out': 2_100_000.0,
            'fee': 0.003,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 4_000_000.0,
            'volume_24h_usd': 8_000_000.0,
            'age_in_blocks': 120,
        },
        {
            'venue': 'quickswap',
            'pair': 'TOKEN → USDC',
            'reserve_in': 2_100_000.0,
            'reserve_out': 2_200_000.0,
            'fee': 0.0025,
            'price_in_usd': 1.0,
            'price_out_usd': 1.0,
            'tvl_usd': 4_200_000.0,
            'volume_24h_usd': 7_500_000.0,
            'age_in_blocks': 100,
        },
    ]

    recorded_c2_route = {}

    original_decide = router.strategies['surgeon'].decide_contract_action

    def capturing_decide(r, raw_spread, min_input, max_input, gas_cost, pending_txs, steps):
        recorded_c2_route['route'] = r
        return original_decide(r, raw_spread, min_input, max_input, gas_cost, pending_txs, steps)

    with patch.object(router.strategies['surgeon'], 'decide_contract_action', side_effect=capturing_decide):
        asyncio.run(router.process_discovery_pipeline(
            route=route,
            raw_spread=0.05,
            gas_cost=1.0,
            steps=4,
        ))

    assert 'route' in recorded_c2_route, "C2 decide_contract_action was never called"
    c2_route = recorded_c2_route['route']

    # C2 must not receive the identical pre-trade route object or values.
    assert c2_route is not route, "C2 received the same route object as C1"
    # At least one reserve must differ from the original, confirming state mutation.
    reserves_changed = any(
        c2_route[i].get('reserve_in') != route[i].get('reserve_in')
        or c2_route[i].get('reserve_out') != route[i].get('reserve_out')
        for i in range(len(route))
    )
    assert reserves_changed, "C2 route reserves are identical to pre-trade state — C1 trade was not applied"