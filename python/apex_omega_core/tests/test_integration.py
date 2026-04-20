import pytest
from apex_omega_core.core.inference import derive_net_edge, profitability_gate
from apex_omega_core.strategies.execution_router import ExecutionRouter

def test_integration():
    # Test end-to-end
    data = {'edge': 0.05}
    result = derive_net_edge(data)
    assert result.net_edge == 0.05

    router = ExecutionRouter()
    exec_result = router.route({'price': 100.0}, 'surgeon')
    assert exec_result.success


def test_derive_net_edge_v7_formula() -> None:
    """derive_net_edge applies the full v7 capital model when v7 keys are present."""
    data = {
        'buy_price': 1.00,
        'buy_slippage': 0.002,
        'sell_price': 1.05,
        'sell_slippage': 0.002,
        'ml_slippage': 0.006,
        'raw_spread': 0.05,
        'buffer_rate': 0.1,
        'trade_size': 50_000.0,
        'fees': 0.001,
    }
    result = derive_net_edge(data)
    # money_out = 1.002, money_in = 1.048, edge = 0.046
    # adjusted_slippage = 0.002, ev_buffer = 0.0025, fees = 0.001
    # net_edge = 0.046 - 0.002 - 0.0025 - 0.001 = 0.0405
    assert result.net_edge == pytest.approx(0.0405)
    feature_names = [f.name for f in result.features]
    assert 'money_in' in feature_names
    assert 'money_out' in feature_names
    assert 'edge' in feature_names
    assert 'adjusted_slippage' in feature_names
    assert 'ev_buffer' in feature_names
    assert 'net_edge' in feature_names


def test_derive_net_edge_legacy_shortcut() -> None:
    """derive_net_edge with only 'edge' key uses legacy shortcut."""
    result = derive_net_edge({'edge': 0.123})
    assert result.net_edge == pytest.approx(0.123)


def test_derive_net_edge_v7_negative() -> None:
    """When costs exceed the spread, net_edge is negative."""
    data = {
        'buy_price': 1.0,
        'buy_slippage': 0.02,
        'sell_price': 1.01,
        'sell_slippage': 0.02,
        'ml_slippage': 0.0,
        'raw_spread': 0.0,
        'buffer_rate': 0.0,
        'trade_size': 0.0,
        'fees': 0.0,
    }
    result = derive_net_edge(data)
    assert result.net_edge < 0.0


# ── profitability_gate (SSOT for P_net × P(fill) > 0) ─────────────────────────

def test_profitability_gate_passes_when_both_positive() -> None:
    """Gate passes only when P_net > 0 and P(fill) > 0."""
    assert profitability_gate(0.01, 0.9) is True
    assert profitability_gate(100.0, 1.0) is True


def test_profitability_gate_fails_when_p_net_zero_or_negative() -> None:
    """Gate must block execution when profit is non-positive."""
    assert profitability_gate(0.0, 0.9) is False
    assert profitability_gate(-1.0, 0.9) is False


def test_profitability_gate_fails_when_p_fill_zero() -> None:
    """Gate must block execution when fill probability is zero."""
    assert profitability_gate(10.0, 0.0) is False


def test_profitability_gate_fails_when_both_zero() -> None:
    assert profitability_gate(0.0, 0.0) is False


# ── derive_net_edge with p_fill ────────────────────────────────────────────────

def test_derive_net_edge_should_execute_true_when_profitable() -> None:
    """should_execute is True when net_edge > 0 and default p_fill = 1.0."""
    data = {
        'buy_price': 1.00, 'buy_slippage': 0.0,
        'sell_price': 1.05, 'sell_slippage': 0.0,
        'ml_slippage': 0.0, 'raw_spread': 0.0,
        'buffer_rate': 0.0, 'trade_size': 0.0, 'fees': 0.0,
    }
    result = derive_net_edge(data)
    assert result.net_edge > 0.0
    assert result.should_execute is True
    assert result.p_fill == pytest.approx(1.0)


def test_derive_net_edge_should_execute_false_when_unprofitable() -> None:
    """should_execute is False when net_edge <= 0."""
    data = {
        'buy_price': 1.0, 'buy_slippage': 0.02,
        'sell_price': 1.01, 'sell_slippage': 0.02,
        'ml_slippage': 0.0, 'raw_spread': 0.0,
        'buffer_rate': 0.0, 'trade_size': 0.0, 'fees': 0.0,
    }
    result = derive_net_edge(data)
    assert result.net_edge < 0.0
    assert result.should_execute is False


def test_derive_net_edge_should_execute_false_when_p_fill_zero() -> None:
    """should_execute is False when p_fill = 0 even if net_edge > 0."""
    data = {
        'buy_price': 1.0, 'buy_slippage': 0.0,
        'sell_price': 1.1, 'sell_slippage': 0.0,
        'ml_slippage': 0.0, 'raw_spread': 0.0,
        'buffer_rate': 0.0, 'trade_size': 0.0, 'fees': 0.0,
    }
    result = derive_net_edge(data, p_fill=0.0)
    assert result.net_edge > 0.0
    assert result.should_execute is False
    assert result.p_fill == pytest.approx(0.0)


def test_derive_net_edge_legacy_shortcut_should_execute() -> None:
    """Legacy shortcut also sets should_execute correctly."""
    result_pos = derive_net_edge({'edge': 0.05})
    assert result_pos.should_execute is True

    result_neg = derive_net_edge({'edge': -0.05})
    assert result_neg.should_execute is False


def test_derive_net_edge_p_fill_propagated() -> None:
    """p_fill supplied to derive_net_edge is exposed on InferenceResult."""
    data = {
        'buy_price': 1.0, 'buy_slippage': 0.0,
        'sell_price': 1.1, 'sell_slippage': 0.0,
        'ml_slippage': 0.0, 'raw_spread': 0.0,
        'buffer_rate': 0.0, 'trade_size': 0.0, 'fees': 0.0,
    }
    result = derive_net_edge(data, p_fill=0.75)
    assert result.p_fill == pytest.approx(0.75)
    assert result.should_execute is True  # net_edge > 0 and p_fill > 0