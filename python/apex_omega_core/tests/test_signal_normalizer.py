"""Tests for signal_normalizer.normalize_signal."""

from __future__ import annotations

import pytest

from apex_omega_core.core.signal_normalizer import normalize_signal, POLYGON_CHAIN_ID


# ---------------------------------------------------------------------------
# POLYGON_CHAIN_ID constant
# ---------------------------------------------------------------------------

def test_polygon_chain_id_value():
    assert POLYGON_CHAIN_ID == 137


# ---------------------------------------------------------------------------
# Rule 1 — chain_id → chainId
# ---------------------------------------------------------------------------

class TestChainIdNormalization:
    def test_chain_id_renamed_to_camel_case(self):
        result = normalize_signal({"chain_id": 137})
        assert result == {"chainId": 137}

    def test_chain_id_int_preserved(self):
        result = normalize_signal({"chain_id": 1})
        assert result["chainId"] == 1

    def test_canonical_chain_id_passthrough(self):
        """If chainId is already present it must survive unchanged."""
        result = normalize_signal({"chainId": 137})
        assert result == {"chainId": 137}
        assert "chain_id" not in result

    def test_both_present_canonical_wins(self):
        """When both chain_id and chainId are present, keep chainId and drop chain_id."""
        result = normalize_signal({"chain_id": 1, "chainId": 137})
        assert result["chainId"] == 137
        assert "chain_id" not in result

    def test_chain_id_not_added_when_absent(self):
        """normalize_signal must not inject chainId when neither key is present."""
        result = normalize_signal({"pair": "WETH/USDC"})
        assert "chainId" not in result
        assert "chain_id" not in result


# ---------------------------------------------------------------------------
# Rule 2 & 3 — execution.input_token → token, execution.input_amount → amount
# ---------------------------------------------------------------------------

class TestExecutionBlockNormalization:
    def test_input_token_lifted_to_top_level(self):
        raw = {"execution": {"input_token": "0xABC"}}
        result = normalize_signal(raw)
        assert result["token"] == "0xABC"
        assert "execution" not in result

    def test_input_amount_lifted_to_top_level(self):
        raw = {"execution": {"input_amount": 5000}}
        result = normalize_signal(raw)
        assert result["amount"] == 5000
        assert "execution" not in result

    def test_both_execution_fields_normalized(self):
        raw = {"execution": {"input_token": "0xDEF", "input_amount": 1000}}
        result = normalize_signal(raw)
        assert result["token"] == "0xDEF"
        assert result["amount"] == 1000
        assert "execution" not in result

    def test_remaining_execution_keys_preserved(self):
        """Unknown keys inside execution should stay in the execution sub-dict."""
        raw = {"execution": {"input_token": "0x1", "input_amount": 100, "slippage_bps": 30}}
        result = normalize_signal(raw)
        assert result["token"] == "0x1"
        assert result["amount"] == 100
        assert result["execution"] == {"slippage_bps": 30}

    def test_canonical_token_not_overwritten(self):
        """If token is already present at top level, existing value is kept."""
        raw = {"token": "0xORIGINAL", "execution": {"input_token": "0xNEW"}}
        result = normalize_signal(raw)
        assert result["token"] == "0xORIGINAL"
        assert "execution" not in result

    def test_canonical_amount_not_overwritten(self):
        raw = {"amount": 9999, "execution": {"input_amount": 1}}
        result = normalize_signal(raw)
        assert result["amount"] == 9999
        assert "execution" not in result

    def test_execution_non_dict_left_alone(self):
        """Non-dict execution value must not be silently dropped."""
        raw = {"execution": "raw_calldata_hex"}
        result = normalize_signal(raw)
        assert result["execution"] == "raw_calldata_hex"


# ---------------------------------------------------------------------------
# Rule 4 — profit.net_usd → metrics.profit_usd
# ---------------------------------------------------------------------------

class TestProfitNormalization:
    def test_net_usd_moved_to_metrics(self):
        raw = {"profit": {"net_usd": 5.23}}
        result = normalize_signal(raw)
        assert result["metrics"]["profit_usd"] == 5.23
        assert "profit" not in result

    def test_profit_block_removed_when_empty_after_extraction(self):
        raw = {"profit": {"net_usd": 1.5}}
        result = normalize_signal(raw)
        assert "profit" not in result

    def test_other_profit_sub_keys_kept(self):
        raw = {"profit": {"net_usd": 2.0, "gross_usd": 3.0}}
        result = normalize_signal(raw)
        assert result["metrics"]["profit_usd"] == 2.0
        assert result["profit"] == {"gross_usd": 3.0}

    def test_existing_metrics_dict_merged(self):
        """net_usd should be added to a pre-existing metrics block."""
        raw = {"profit": {"net_usd": 4.0}, "metrics": {"spread_bps": 120}}
        result = normalize_signal(raw)
        assert result["metrics"]["profit_usd"] == 4.0
        assert result["metrics"]["spread_bps"] == 120

    def test_existing_metrics_profit_usd_not_overwritten(self):
        raw = {"profit": {"net_usd": 1.0}, "metrics": {"profit_usd": 99.0}}
        result = normalize_signal(raw)
        assert result["metrics"]["profit_usd"] == 99.0

    def test_profit_non_dict_left_alone(self):
        raw = {"profit": 5.0}
        result = normalize_signal(raw)
        assert result["profit"] == 5.0
        assert "metrics" not in result


# ---------------------------------------------------------------------------
# Combined / real-world scenarios
# ---------------------------------------------------------------------------

class TestCombinedNormalization:
    def test_full_legacy_signal(self):
        raw = {
            "chain_id": 137,
            "execution": {"input_token": "0xUSDC", "input_amount": 10_000},
            "profit": {"net_usd": 12.5},
        }
        result = normalize_signal(raw)
        assert result == {
            "chainId": 137,
            "token": "0xUSDC",
            "amount": 10_000,
            "metrics": {"profit_usd": 12.5},
        }

    def test_canonical_signal_passthrough(self):
        raw = {
            "chainId": 137,
            "token": "0xWETH",
            "amount": 5_000,
            "metrics": {"profit_usd": 7.0},
        }
        result = normalize_signal(raw)
        assert result == raw

    def test_extra_top_level_keys_preserved(self):
        raw = {
            "chain_id": 137,
            "pair": "WETH/USDC",
            "spread_bps": 42,
            "execution": {"input_token": "0x1", "input_amount": 500},
            "profit": {"net_usd": 0.5},
        }
        result = normalize_signal(raw)
        assert result["pair"] == "WETH/USDC"
        assert result["spread_bps"] == 42
        assert result["chainId"] == 137
        assert result["token"] == "0x1"
        assert result["amount"] == 500
        assert result["metrics"]["profit_usd"] == 0.5

    def test_input_not_mutated(self):
        raw = {"chain_id": 137, "execution": {"input_token": "0xABC"}}
        original_keys = set(raw.keys())
        normalize_signal(raw)
        assert set(raw.keys()) == original_keys

    def test_empty_signal_returns_empty(self):
        assert normalize_signal({}) == {}

    def test_unrelated_keys_passthrough(self):
        raw = {"foo": "bar", "baz": 42}
        assert normalize_signal(raw) == raw
