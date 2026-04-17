import pytest
from apex_omega_core.core.inference import derive_net_edge
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