import pytest
from apex_omega_core.core.slippage_sentinel import SlippageSentinel


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

def test_compute_net_edge_v7_p_fill_blocks_execution() -> None:
    """should_execute is False when p_fill = 0 even with positive net_edge."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.0,
        buy_slippage=0.0,
        sell_price=1.1,
        sell_slippage=0.0,
        ml_slippage=0.0,
        raw_spread=0.0,
        buffer_rate=0.0,
        trade_size=0.0,
        fees=0.0,
        p_fill=0.0,
    )
    assert result['net_edge'] > 0.0
    assert result['should_execute'] is False
    assert result['p_fill'] == pytest.approx(0.0)


def test_compute_net_edge_v7_p_fill_default_backward_compat() -> None:
    """Default p_fill = 1.0 preserves existing behaviour for positive net_edge."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.0,
        buy_slippage=0.0,
        sell_price=1.1,
        sell_slippage=0.0,
        ml_slippage=0.0,
        raw_spread=0.0,
        buffer_rate=0.0,
        trade_size=0.0,
        fees=0.0,
    )
    assert result['should_execute'] is True
    assert result['p_fill'] == pytest.approx(1.0)


def test_compute_net_edge_v7_p_fill_in_result() -> None:
    """p_fill is always returned in the result dict."""
    sentinel = SlippageSentinel()
    result = sentinel.compute_net_edge_v7(
        buy_price=1.0, buy_slippage=0.0,
        sell_price=1.05, sell_slippage=0.0,
        ml_slippage=0.0, raw_spread=0.0,
        buffer_rate=0.0, trade_size=0.0, fees=0.0,
        p_fill=0.85,
    )
    assert 'p_fill' in result
    assert result['p_fill'] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# two_leg_arb_profit — canonical two-swap AMM fee correctness
# ---------------------------------------------------------------------------

class TestTwoLegArbProfit:
    """Verify spec-locked invariants for the canonical two-swap arbitrage calculation.

    The canonical form (carved in stone):
        B_out_1 = (A_in*(1-f1)*R1_out) / (R1_in + A_in*(1-f1))
        A_out_2 = (B_out_1*(1-f2)*R2_out) / (R2_in + B_out_1*(1-f2))
        P_gross = A_out_2 - A_in
        P_net   = P_gross - C_gas - C_loan - C_other
    """

    def setup_method(self):
        self.sentinel = SlippageSentinel()

    # ── Result structure ──────────────────────────────────────────────────────

    def test_returns_expected_keys(self):
        result = self.sentinel.two_leg_arb_profit(
            a_in=1_000.0, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        assert {'b_out_1', 'a_out_2', 'p_gross', 'p_net', 'owner_submission_edge'} == set(result.keys())

    # ── Phase C invariant: Swap 2 input is exactly Swap 1 output ─────────────

    def test_swap2_input_is_swap1_output(self):
        """b_out_1 (mid-asset inventory) feeds directly into Swap 2 — no extra haircut."""
        a_in = 5_000.0
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        # Recompute Swap 2 from b_out_1 independently to verify the handoff.
        b_out_1 = result['b_out_1']
        expected_a_out_2 = self.sentinel.amm_swap(b_out_1, 1_020_000.0, 1_060_000.0, 0.0025)
        assert result['a_out_2'] == pytest.approx(expected_a_out_2)

    # ── Fee-basis invariant: fee1 on A, fee2 on B_out_1 (different amounts) ──

    def test_fee1_applied_to_a_in_not_b(self):
        """fee1 reduces the A_in going into Swap 1; it is NOT applied to the B output."""
        a_in = 1_000.0
        fee1 = 0.003
        r1_in, r1_out = 1_000_000.0, 1_020_000.0
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=fee1, r1_in=r1_in, r1_out=r1_out,
            fee2=0.0, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        # Expected Swap 1 output from first principles.
        a_eff = a_in * (1.0 - fee1)
        expected_b = (a_eff * r1_out) / (r1_in + a_eff)
        assert result['b_out_1'] == pytest.approx(expected_b)

    def test_fee2_applied_to_b_out_1_not_a_in(self):
        """fee2 is charged on b_out_1 (B units), not on the original A input."""
        a_in = 1_000.0
        r1_in, r1_out = 1_000_000.0, 1_020_000.0
        r2_in, r2_out = 1_020_000.0, 1_060_000.0
        fee2 = 0.0025

        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=r1_in, r1_out=r1_out,
            fee2=fee2, r2_in=r2_in, r2_out=r2_out,
        )
        b_out_1 = result['b_out_1']
        # fee2 applies to b_out_1, not to a_in.
        b_eff = b_out_1 * (1.0 - fee2)
        expected_a_out = (b_eff * r2_out) / (r2_in + b_eff)
        assert result['a_out_2'] == pytest.approx(expected_a_out)

    def test_fee_bases_are_different_amounts(self):
        """fee1 base (A_in) and fee2 base (B_out_1) are not the same amount."""
        a_in = 1_000.0
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        # B_out_1 is in WETH units; A_in is in USDC units — they must differ.
        assert result['b_out_1'] != pytest.approx(a_in)

    # ── No manual slippage subtraction between swaps ──────────────────────────

    def test_no_extra_slippage_deduction_between_swaps(self):
        """AMM output of Swap 1 feeds Swap 2 in full — slippage is embedded, not subtracted."""
        a_in = 2_000.0
        # With fee2=0 we can isolate that b_out_1 is used unchanged as Swap 2 input.
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=500_000.0, r1_out=510_000.0,
            fee2=0.0, r2_in=510_000.0, r2_out=530_000.0,
        )
        b_out_1 = result['b_out_1']
        # With fee2=0, swap2 should give exactly: b_out_1 * r2_out / (r2_in + b_out_1)
        expected_a_out = (b_out_1 * 530_000.0) / (510_000.0 + b_out_1)
        assert result['a_out_2'] == pytest.approx(expected_a_out)

    # ── P_gross and P_net correctness ─────────────────────────────────────────

    def test_p_gross_equals_a_out_minus_a_in(self):
        a_in = 1_000.0
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        assert result['p_gross'] == pytest.approx(result['a_out_2'] - a_in)

    def test_p_net_deducts_token_costs_and_tracks_owner_gas(self):
        a_in = 1_000.0
        result = self.sentinel.two_leg_arb_profit(
            a_in=a_in, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
            c_gas=2.5, c_loan=1.0, c_other=0.5,
        )
        assert result['p_net'] == pytest.approx(result['p_gross'] - 1.0 - 0.5)
        assert result['owner_submission_edge'] == pytest.approx(result['p_net'] - 2.5)

    def test_zero_costs_p_net_equals_p_gross(self):
        result = self.sentinel.two_leg_arb_profit(
            a_in=1_000.0, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        assert result['p_net'] == pytest.approx(result['p_gross'])

    # ── Profitable when spread exceeds fees ───────────────────────────────────

    def test_profitable_when_spread_exceeds_fees(self):
        """Deep pools with meaningful spread: p_gross > 0."""
        result = self.sentinel.two_leg_arb_profit(
            a_in=10_000.0, fee1=0.003, r1_in=10_000_000.0, r1_out=10_400_000.0,
            fee2=0.0025, r2_in=10_400_000.0, r2_out=10_800_000.0,
        )
        assert result['p_gross'] > 0.0

    def test_symmetric_pools_zero_spread_produces_loss(self):
        """With no price advantage, fees consume capital and p_gross < 0."""
        result = self.sentinel.two_leg_arb_profit(
            a_in=1_000.0, fee1=0.003, r1_in=1_000_000.0, r1_out=1_000_000.0,
            fee2=0.003, r2_in=1_000_000.0, r2_out=1_000_000.0,
        )
        assert result['p_gross'] < 0.0

    # ── Degenerate inputs ─────────────────────────────────────────────────────

    def test_zero_input_returns_zero_profit(self):
        result = self.sentinel.two_leg_arb_profit(
            a_in=0.0, fee1=0.003, r1_in=1_000_000.0, r1_out=1_020_000.0,
            fee2=0.0025, r2_in=1_020_000.0, r2_out=1_060_000.0,
        )
        assert result['b_out_1'] == pytest.approx(0.0)
        assert result['a_out_2'] == pytest.approx(0.0)
        assert result['p_gross'] == pytest.approx(0.0)
        assert result['p_net'] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# evaluate_slippage — base_slippage_bps denominator correctness
# ---------------------------------------------------------------------------

class TestEvaluateSlippageDenominator:
    """Verify that base_slippage_bps uses expected_out (not amount_in) as denominator.

    The core invariant: base_slippage_bps must equal
        (expected_out − actual_out) / expected_out × 10_000
    where expected_out = amount_in × (reserve_out / reserve_in).

    Using amount_in as the denominator was wrong for any pool whose exchange
    rate is not 1:1 (e.g. USDC/WETH), because amount_in is in input-token
    units while base_output is in output-token units — different currencies.
    """

    def setup_method(self):
        self.sentinel = SlippageSentinel()

    def _expected_bps(self, amount_in, reserve_in, reserve_out, fee_bps):
        """Reference implementation of the corrected formula."""
        fee_factor = 1.0 - fee_bps / 10_000.0
        amount_after_fee = amount_in * fee_factor
        denom = reserve_in + amount_after_fee
        base_output = (amount_after_fee * reserve_out) / denom
        expected_out = amount_in * (reserve_out / reserve_in)
        return max(0.0, (expected_out - base_output) / expected_out) * 10_000.0

    def test_par_pool_denominator_matches_corrected_formula(self):
        """For a 1:1 pool the old and new denominators coincide; result must be consistent."""
        amount_in = 10_000.0
        reserve_in = reserve_out = 1_000_000.0
        fee_bps = 30.0
        predicted, _, _ = self.sentinel.evaluate_slippage(
            amount_in=amount_in, reserve_in=reserve_in, reserve_out=reserve_out,
            fee_bps=fee_bps, active_liquidity=500_000.0,
            vol_1h=0.0, vol_24h=0.0, observed_spread_bps=9999.0,
            gas_cost_usd=0.0, loan_amount_usd=1.0,
        )
        base_bps = self._expected_bps(amount_in, reserve_in, reserve_out, fee_bps)
        # predicted includes vol/ml residual terms (both 0 here), so it equals base_bps.
        assert predicted == pytest.approx(base_bps, rel=1e-6)

    def test_non_par_pool_uses_expected_out_denominator(self):
        """For a USDC/WETH-style pool (rate far from 1:1), the corrected formula
        must return a small, positive bps value equal to the reference formula.

        The old formula (a_in − base_output)/a_in produced a nonsensical value because
        a_in is in USDC and base_output is in WETH — different currencies.
        """
        # Simulate a pool where 1 USDC buys ~0.0003 WETH  (rate ≈ 3333 USDC/WETH)
        amount_in = 10_000.0          # USDC
        reserve_in = 5_000_000.0     # USDC reserve
        reserve_out = 1_500.0        # WETH reserve  → rate ≈ 3333 USDC/WETH
        fee_bps = 30.0
        predicted, _, _ = self.sentinel.evaluate_slippage(
            amount_in=amount_in, reserve_in=reserve_in, reserve_out=reserve_out,
            fee_bps=fee_bps, active_liquidity=2_000_000.0,
            vol_1h=0.0, vol_24h=0.0, observed_spread_bps=9999.0,
            gas_cost_usd=0.0, loan_amount_usd=1.0,
        )
        base_bps = self._expected_bps(amount_in, reserve_in, reserve_out, fee_bps)
        # predicted ≈ base_bps (vol/ml terms are 0).
        assert predicted == pytest.approx(base_bps, rel=1e-6)
        # The corrected formula gives a small positive bps figure — well under
        # 100 bps for a 1% trade-size-to-reserve ratio with a 30-bps fee.
        # The old formula (a_in − base_output)/a_in gave ~10,000 bps here
        # because it subtracted WETH output from USDC input.
        assert 0.0 <= predicted < 100.0

    def test_base_slippage_bps_increases_with_trade_size(self):
        """Larger trades incur more price impact — base_slippage_bps must be strictly
        monotone increasing with amount_in (holding reserves and fee constant)."""
        reserve_in = 1_000_000.0
        reserve_out = 2_000.0   # non-par pool
        fee_bps = 30.0
        sizes = [1_000.0, 5_000.0, 20_000.0]
        prev = -1.0
        for size in sizes:
            predicted, _, _ = self.sentinel.evaluate_slippage(
                amount_in=size, reserve_in=reserve_in, reserve_out=reserve_out,
                fee_bps=fee_bps, active_liquidity=500_000.0,
                vol_1h=0.0, vol_24h=0.0, observed_spread_bps=9999.0,
                gas_cost_usd=0.0, loan_amount_usd=1.0,
            )
            assert predicted > prev
            prev = predicted

    def test_zero_vol_slippage_equals_pure_amm_impact(self):
        """With zero volatility the predicted slippage is the base AMM impact alone."""
        amount_in = 5_000.0
        reserve_in = 500_000.0
        reserve_out = 500_000.0
        fee_bps = 30.0
        predicted, _, _ = self.sentinel.evaluate_slippage(
            amount_in=amount_in, reserve_in=reserve_in, reserve_out=reserve_out,
            fee_bps=fee_bps, active_liquidity=250_000.0,
            vol_1h=0.0, vol_24h=0.0, observed_spread_bps=9999.0,
            gas_cost_usd=0.0, loan_amount_usd=1.0,
        )
        base_bps = self._expected_bps(amount_in, reserve_in, reserve_out, fee_bps)
        assert predicted == pytest.approx(base_bps, rel=1e-6)

    def test_execute_condition_dimensionally_consistent(self):
        """observed_spread_bps and predicted_slippage_bps are now on the same basis
        (bps of capital A).  A spread that clearly exceeds costs must trigger execution."""
        _, should_execute, _ = self.sentinel.evaluate_slippage(
            amount_in=1_000.0, reserve_in=1_000_000.0, reserve_out=500.0,
            fee_bps=30.0, active_liquidity=500_000.0,
            vol_1h=0.001, vol_24h=0.001, observed_spread_bps=5_000.0,
            gas_cost_usd=0.01, loan_amount_usd=10_000.0,
        )
        assert should_execute is True

    def test_degenerate_zero_reserve_in_returns_guard(self):
        result = self.sentinel.evaluate_slippage(
            amount_in=1_000.0, reserve_in=0.0, reserve_out=500_000.0,
            fee_bps=30.0, active_liquidity=100_000.0,
            vol_1h=0.0, vol_24h=0.0, observed_spread_bps=9999.0,
            gas_cost_usd=0.0, loan_amount_usd=1.0,
        )
        assert result == (999_999.0, False, 999_999.0)


# ---------------------------------------------------------------------------
# build_execution_slippage — USD-consistent difference calculation
# ---------------------------------------------------------------------------

class TestBuildExecutionSlippage:
    """Verify that build_execution_slippage expresses all monetary quantities in USD.

    The ``difference`` field must be:
        execution_delta (USD) − total_leg_slippage_usd (USD)
    where total_leg_slippage_usd = Σ slippage_fraction_i × usd_in_i.

    The old code subtracted dimensionless slippage fractions (e.g. 0.003) from a
    USD delta — a unit error that produced nonsensical results.
    """

    def setup_method(self):
        self.sentinel = SlippageSentinel()

    def _make_sentinel_output(self, initial_usd_in, final_usd_out, legs):
        """Build the minimal sentinel_output dict used by build_execution_slippage."""
        return {
            'optimal_input': initial_usd_in,
            'final_output': final_usd_out,
            'initial_usd_in': initial_usd_in,
            'final_usd_out': final_usd_out,
            'slippage_per_leg': legs,
        }

    def test_difference_is_in_usd(self):
        """difference = execution_delta − (Σ slippage_i × usd_in_i) [all USD]."""
        legs = [
            {'slippage': 0.003, 'usd_in': 10_000.0},
            {'slippage': 0.002, 'usd_in':  9_950.0},
        ]
        out = self._make_sentinel_output(10_000.0, 9_890.0, legs)
        slippage_obj = self.sentinel.build_execution_slippage(out)

        execution_delta = 9_890.0 - 10_000.0          # -110 USD
        total_usd_slip = 0.003 * 10_000.0 + 0.002 * 9_950.0  # 30 + 19.9 = 49.9 USD
        expected_diff = execution_delta - total_usd_slip       # -110 - 49.9 = -159.9 USD

        assert slippage_obj.expected_price == pytest.approx(10_000.0)
        assert slippage_obj.actual_price   == pytest.approx(9_890.0)
        assert slippage_obj.difference     == pytest.approx(expected_diff, rel=1e-9)

    def test_no_slippage_difference_equals_execution_delta(self):
        """When all slippage fractions are 0, difference == execution_delta."""
        legs = [
            {'slippage': 0.0, 'usd_in': 5_000.0},
            {'slippage': 0.0, 'usd_in': 4_980.0},
        ]
        out = self._make_sentinel_output(5_000.0, 4_980.0, legs)
        slippage_obj = self.sentinel.build_execution_slippage(out)
        assert slippage_obj.difference == pytest.approx(4_980.0 - 5_000.0)

    def test_missing_slippage_key_treated_as_zero(self):
        """Legs without a 'slippage' key must contribute zero to the total."""
        legs = [{'usd_in': 1_000.0}, {'slippage': 0.01, 'usd_in': 990.0}]
        out = self._make_sentinel_output(1_000.0, 979.0, legs)
        slippage_obj = self.sentinel.build_execution_slippage(out)
        execution_delta = 979.0 - 1_000.0
        expected_diff = execution_delta - (0.0 * 1_000.0 + 0.01 * 990.0)
        assert slippage_obj.difference == pytest.approx(expected_diff, rel=1e-9)

    def test_missing_usd_in_key_treated_as_zero(self):
        """Legs without 'usd_in' must contribute zero to the total."""
        legs = [{'slippage': 0.005}]
        out = self._make_sentinel_output(500.0, 490.0, legs)
        slippage_obj = self.sentinel.build_execution_slippage(out)
        # slippage contribution = 0.005 × 0.0 = 0.0
        assert slippage_obj.difference == pytest.approx(490.0 - 500.0)
