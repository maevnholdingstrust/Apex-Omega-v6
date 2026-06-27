import pytest

from dry_run import (
    _assert_real_market_data_only_policy,
    _leg_price_has_structural_failure,
    _leg_price_invariant,
    _simulate_pools,
)


def test_leg_price_invariant_accepts_executable_edge():
    proof = _leg_price_invariant(
        leg_amounts_in=[100.0, 50.0],
        leg_amounts_out=[50.0, 103.0],
        quote_block=123,
    )

    assert proof["leg_price_invariant_status"] == "LEG_PRICE_EDGE_VALID"
    assert proof["leg_price_invariant_reason"] == "BUY_LEG1_PRICE_LT_SELL_LEG2_PRICE"
    assert proof["buy_leg1_price"] == 2.0
    assert proof["sell_leg2_price"] == 2.06
    assert proof["leg_price_quote_block"] == 123


def test_leg_price_invariant_reports_inverted_edge_as_diagnostic():
    proof = _leg_price_invariant(
        leg_amounts_in=[100.0, 50.0],
        leg_amounts_out=[50.0, 99.0],
    )

    assert proof["leg_price_invariant_status"] == "LEG_PRICE_EDGE_INVERTED_DIAGNOSTIC"
    assert proof["leg_price_invariant_reason"] == "BUY_LEG1_PRICE_NOT_BELOW_SELL_LEG2_PRICE"
    assert not _leg_price_has_structural_failure(proof)


def test_leg_price_invariant_rejects_missing_amounts():
    proof = _leg_price_invariant(leg_amounts_in=[100.0], leg_amounts_out=[50.0])

    assert proof["leg_price_invariant_status"] == "NO_EXECUTABLE_PRICE_EDGE"
    assert proof["leg_price_invariant_reason"] == "LEG_PRICE_AMOUNTS_MISSING"
    assert _leg_price_has_structural_failure(proof)


def test_leg_price_invariant_rejects_leg2_input_above_leg1_output():
    proof = _leg_price_invariant(
        leg_amounts_in=[100.0, 51.0],
        leg_amounts_out=[50.0, 106.0],
    )

    assert proof["leg_price_invariant_status"] == "NO_EXECUTABLE_PRICE_EDGE"
    assert proof["leg_price_invariant_reason"] == "LEG2_INPUT_EXCEEDS_LEG1_EXECUTABLE_OUTPUT"
    assert _leg_price_has_structural_failure(proof)


def test_real_market_data_policy_bans_synthetic_pool_generation(monkeypatch):
    monkeypatch.setenv("REAL_MARKET_DATA_ONLY", "true")
    monkeypatch.setenv("APEX_ALLOW_SYNTHETIC_TEST_DATA", "true")

    with pytest.raises(RuntimeError, match="REAL_MARKET_DATA_ONLY_VIOLATION"):
        _assert_real_market_data_only_policy()

    with pytest.raises(RuntimeError, match="SYNTHETIC_POOL_DATA_DISABLED"):
        _simulate_pools(1)
