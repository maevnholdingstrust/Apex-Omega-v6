import pytest

from apex_omega_core.core.multi_market_scanner import ScannerOpportunity
from apex_omega_core.core.scanner_strategy_pipeline import (
    _build_v2_dynamic_candidate,
    _raw_from_usd,
)


def test_raw_from_usd_usdc() -> None:
    assert _raw_from_usd(100.0, "USDCe") == 100_000_000


def test_raw_from_usd_wmatic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEX_POL_USD", "0.5")
    assert _raw_from_usd(100.0, "WMATIC") == 200 * 10**18


def test_raw_from_usd_weth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEX_ETH_USD", "2500")
    assert _raw_from_usd(100.0, "WETH") == 40_000_000_000_000_000


def test_raw_from_usd_wbtc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEX_BTC_USD", "50000")
    assert _raw_from_usd(100.0, "WBTC") == 200_000


def test_leg2_amount_in_uses_leg1_expected_output_native_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEX_POL_USD", "1")
    monkeypatch.setenv("APEX_ETH_USD", "2000")
    opportunity = ScannerOpportunity(
        base_symbol="WETH",
        quote_symbol="WMATIC",
        buy_venue="quickswap_v2",
        sell_venue="sushiswap_v2",
        buy_pool="0x1111111111111111111111111111111111111111",
        sell_pool="0x2222222222222222222222222222222222222222",
        buy_price=2000.0,
        sell_price=2100.0,
        raw_spread_bps=500.0,
        execution_supported=True,
    )

    build = _build_v2_dynamic_candidate(
        opportunity,
        executor_address="0x3333333333333333333333333333333333333333",
        min_net_profit_usd=0.1,
        gas_cost_usd=0.0,
        flash_fee_bps=0.0,
        risk_buffer_usd=0.0,
        minout_buffer_bps=25.0,
    )
    assert build.strikeable
    strategy = build.strategy_output
    assert strategy is not None
    steps = strategy["steps"]
    leg1_expected_out = strategy["opportunity"]["leg1_out_tokens"]
    assert steps[1]["minAmountIn"] == int(leg1_expected_out * (10**18))
    assert strategy["opportunity"]["loan_amount_raw"] == steps[0]["minAmountIn"]
    assert strategy["min_profit"] == _raw_from_usd(
        strategy["opportunity"]["net_profit_usd"],
        "WMATIC",
    )
