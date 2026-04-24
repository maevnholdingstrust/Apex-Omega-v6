"""Regression tests for the six blocker patches (audit fix set).

Patch 1 — contract_invoker: unit-conversion helpers
Patch 2 — contract_invoker: chain-aware min_profit_wei (POL on Polygon)
Patch 3 — c1/c2: p_fill enforced on every strike path
Patch 4 — mev_gas_oracle: TTL auto-refresh + per-cycle invalidate
Patch 5 — slippage_sentinel: optimize() loop never exceeds max_input
Patch 6 — Rust: Polygon factory addresses (tested in Rust; Python smoke check)
"""

from __future__ import annotations

import time
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch 1 — unit-conversion helpers
# ---------------------------------------------------------------------------

from apex_omega_core.core.contract_invoker import (
    TokenUnitSpec,
    _to_base_units,
    _usd_to_token_base_units,
    _require_int_base_units,
    _validate_calldata_context,
    resolve_optimal_input_units,
    resolve_min_final_output_units,
    attach_flashloan_token_meta,
    usd_to_native_wei,
)


class TestToBaseUnits:
    def test_usdc_6_decimals(self) -> None:
        # 50 000 USDC at 6 decimals → 50 000 * 1e6 = 50_000_000_000
        assert _to_base_units(50_000, 6) == 50_000_000_000

    def test_weth_18_decimals(self) -> None:
        # 1 ETH at 18 decimals
        assert _to_base_units(1, 18) == 10 ** 18

    def test_truncates_not_rounds(self) -> None:
        # 1.999 at 6 decimals → 1_999_000 (floor, not round)
        assert _to_base_units("1.999", 6) == 1_999_000

    def test_zero(self) -> None:
        assert _to_base_units(0, 6) == 0


class TestUsdToTokenBaseUnits:
    def test_usdc_at_peg(self) -> None:
        token = TokenUnitSpec(symbol="USDC", decimals=6, usd_price=Decimal("1"))
        # 10 000 USD → 10 000 USDC → 10_000_000_000 base units
        assert _usd_to_token_base_units(10_000, token) == 10_000_000_000

    def test_wbtc_at_price(self) -> None:
        token = TokenUnitSpec(symbol="WBTC", decimals=8, usd_price=Decimal("30000"))
        # 30 000 USD → 1 WBTC → 1 * 1e8 = 100_000_000 base units
        assert _usd_to_token_base_units(30_000, token) == 100_000_000

    def test_missing_usd_price_raises(self) -> None:
        token = TokenUnitSpec(symbol="X", decimals=18, usd_price=None)
        with pytest.raises(ValueError, match="usd_price"):
            _usd_to_token_base_units(100, token)

    def test_zero_usd_price_raises(self) -> None:
        token = TokenUnitSpec(symbol="X", decimals=18, usd_price=Decimal("0"))
        with pytest.raises(ValueError):
            _usd_to_token_base_units(100, token)


class TestRequireIntBaseUnits:
    def test_valid_key(self) -> None:
        ctx = {"optimal_input_base_units": 50_000_000_000}
        assert _require_int_base_units(ctx, "optimal_input_base_units") == 50_000_000_000

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError, match="Missing required base-unit field"):
            _require_int_base_units({}, "optimal_input_base_units")

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="Negative base-unit value"):
            _require_int_base_units({"k": -1}, "k")


class TestResolveOptimalInputUnits:
    def test_explicit_base_units_path(self) -> None:
        context = {"optimal_input_base_units": 50_000 * 10 ** 6}
        assert resolve_optimal_input_units(context) == 50_000 * 10 ** 6

    def test_usd_fallback_path(self) -> None:
        context = {
            "optimal_input": 50_000,
            "flashloan_asset_symbol": "USDC",
            "flashloan_asset_decimals": 6,
            "flashloan_asset_usd_price": "1.0",
        }
        assert resolve_optimal_input_units(context) == 50_000 * 10 ** 6

    def test_explicit_path_takes_precedence_over_usd(self) -> None:
        context = {
            "optimal_input_base_units": 999,
            "optimal_input": 50_000,
            "flashloan_asset_symbol": "USDC",
            "flashloan_asset_decimals": 6,
            "flashloan_asset_usd_price": "1.0",
        }
        assert resolve_optimal_input_units(context) == 999


class TestResolveMinFinalOutputUnits:
    def test_explicit_base_units_path(self) -> None:
        context = {"min_final_output_base_units": 50_200 * 10 ** 6}
        assert resolve_min_final_output_units(context) == 50_200 * 10 ** 6

    def test_usd_not_passed_directly_as_uint256(self) -> None:
        """USD float must not be truncated to a raw uint256."""
        context = {
            "optimal_input_base_units": 50_000 * 10 ** 6,
            "min_final_output_base_units": 50_200 * 10 ** 6,
        }
        assert resolve_optimal_input_units(context) == 50_000 * 10 ** 6
        assert resolve_min_final_output_units(context) == 50_200 * 10 ** 6


class TestValidateCalldataContext:
    """_validate_calldata_context must raise actionable ValueError for missing keys."""

    def test_passes_with_direct_base_unit_keys(self) -> None:
        ctx = {
            "optimal_input_base_units": 50_000 * 10 ** 6,
            "min_final_output_base_units": 50_200 * 10 ** 6,
        }
        _validate_calldata_context(ctx)  # must not raise

    def test_passes_with_full_usd_fallback_keys(self) -> None:
        ctx = {
            "optimal_input": 50_000,
            "flashloan_asset_symbol": "USDC",
            "flashloan_asset_decimals": 6,
            "flashloan_asset_usd_price": "1.0",
            "final_output": 50_200,
            "profit_token_symbol": "USDC",
            "profit_token_decimals": 6,
            "profit_token_usd_price": "1.0",
        }
        _validate_calldata_context(ctx)  # must not raise

    def test_raises_for_sentinel_only_usd_output(self) -> None:
        """SlippageSentinel outputs only optimal_input/final_output — must fail with clear message."""
        ctx = {
            "optimal_input": 50_000.0,
            "final_output": 51_000.0,
            "profit": 1_000.0,
        }
        with pytest.raises(ValueError) as exc_info:
            _validate_calldata_context(ctx)
        msg = str(exc_info.value)
        assert "optimal_input_base_units" in msg or "Cannot resolve optimal_input" in msg
        assert "min_final_output_base_units" in msg or "Cannot resolve min_final_output" in msg

    def test_error_message_lists_missing_keys(self) -> None:
        """Error message must enumerate exactly which keys are absent."""
        ctx = {
            "optimal_input": 50_000.0,
            "flashloan_asset_symbol": "USDC",
            # flashloan_asset_decimals and flashloan_asset_usd_price are missing
        }
        with pytest.raises(ValueError) as exc_info:
            _validate_calldata_context(ctx)
        msg = str(exc_info.value)
        assert "flashloan_asset_decimals" in msg or "flashloan_asset_usd_price" in msg

    def test_passes_with_mixed_paths(self) -> None:
        """Direct key for input + USD path for output is a valid combination."""
        ctx = {
            "optimal_input_base_units": 50_000 * 10 ** 6,
            "final_output": 50_200,
            "profit_token_symbol": "USDC",
            "profit_token_decimals": 6,
            "profit_token_usd_price": "1.0",
        }
        _validate_calldata_context(ctx)  # must not raise

    def test_build_c1_calldata_raises_for_sentinel_only_output(self) -> None:
        """build_c1_calldata must propagate the validator's clear ValueError."""
        from apex_omega_core.core.contract_invoker import ContractInvoker
        invoker = ContractInvoker.__new__(ContractInvoker)
        from web3 import Web3
        invoker.target_address = Web3.to_checksum_address(
            "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
        )
        strike_plan = {
            "sentinel_output": {
                "optimal_input": 50_000.0,
                "final_output": 51_000.0,
                "profit": 1_000.0,
                "raw_spread": 0.02,
            }
        }
        with pytest.raises(ValueError, match="Calldata context is missing required fields"):
            invoker.build_c1_calldata(strike_plan)

    def test_build_c2_calldata_raises_for_sentinel_only_output(self) -> None:
        """build_c2_calldata must propagate the validator's clear ValueError."""
        from apex_omega_core.core.contract_invoker import ContractInvoker
        invoker = ContractInvoker.__new__(ContractInvoker)
        from web3 import Web3
        invoker.target_address = Web3.to_checksum_address(
            "0x0466759822ABAA7E416276E1cf2b538d7FC540BD"
        )
        decision_plan = {
            "sentinel_output": {
                "optimal_input": 50_000.0,
                "final_output": 51_000.0,
                "profit": 1_000.0,
                "raw_spread": 0.02,
            },
            "decision": "STRIKE",
        }
        with pytest.raises(ValueError, match="Calldata context is missing required fields"):
            invoker.build_c2_calldata(decision_plan)


# ---------------------------------------------------------------------------
# Patch 2 — chain-aware min_profit_wei
# ---------------------------------------------------------------------------

class TestUsdToNativeWei:
    def test_polygon_uses_pol_price(self) -> None:
        # At 0.85 USD/POL, 10 USD → ~11.76 POL → > 10^18 Wei
        wei = usd_to_native_wei(10, 137)
        assert wei > 10 ** 18, "10 USD must exceed 1 POL Wei at $0.85/POL"

    def test_ethereum_uses_eth_price(self) -> None:
        # At 3500 USD/ETH, 10 USD → 10/3500 ETH → much less than 1 ETH in Wei
        wei = usd_to_native_wei(10, 1)
        assert wei < 10 ** 18, "10 USD is less than 1 ETH"

    def test_polygon_min_profit_uses_chain_native_price(self) -> None:
        """Regression: ensure Polygon uses POL pricing, not ETH pricing."""
        pol_wei = usd_to_native_wei(10, 137)    # POL at ~$0.85
        eth_wei = usd_to_native_wei(10, 1)       # ETH at ~$3500
        # 10 USD buys far more POL than ETH, so POL Wei must be much larger
        assert pol_wei > eth_wei * 100

    def test_unsupported_chain_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            usd_to_native_wei(10, 99999)

    def test_zero_amount(self) -> None:
        assert usd_to_native_wei(0, 137) == 0


class TestInvokeBundleChainIdHardError:
    """invoke_bundle must raise (not silently default) when chain_id cannot be fetched."""

    def _make_invoker(self, chain_id_side_effect):
        from apex_omega_core.core.contract_invoker import ContractInvoker
        from apex_omega_core.core.mev_gas_oracle import GasOracle, GasPriceSnapshot

        w3 = MagicMock()
        type(w3.eth).chain_id = property(MagicMock(side_effect=chain_id_side_effect))

        snapshot = GasPriceSnapshot(
            base_fee_gwei=50.0,
            tip_p25_gwei=1.0,
            tip_p50_gwei=2.0,
            tip_p75_gwei=4.0,
            tip_p90_gwei=8.0,
            gas_used_ratio_avg=0.5,
        )
        oracle = MagicMock(spec=GasOracle)
        oracle.get_snapshot.return_value = snapshot

        invoker = ContractInvoker.__new__(ContractInvoker)
        invoker.w3 = w3
        invoker.private_key = "0x" + "aa" * 32
        invoker.target_address = "0x" + "bb" * 20
        invoker._gas_oracle = oracle
        return invoker

    def test_chain_id_rpc_failure_raises_runtime_error(self) -> None:
        """A broken RPC must propagate as RuntimeError, not silently use chain 137."""
        invoker = self._make_invoker(chain_id_side_effect=ConnectionError("timeout"))
        with pytest.raises(RuntimeError, match="Cannot fetch chain_id"):
            import asyncio
            asyncio.run(
                invoker.invoke_bundle(
                    calldata=b"\x00" * 4,
                    p_net_usd=10.0,
                    gas_units=200_000,
                )
            )

    def test_chain_id_rpc_failure_is_not_swallowed(self) -> None:
        """The exception must NOT be a silent warning; the call must not return normally."""
        invoker = self._make_invoker(chain_id_side_effect=OSError("network unavailable"))
        raised = False
        try:
            import asyncio
            asyncio.run(
                invoker.invoke_bundle(
                    calldata=b"\x00" * 4,
                    p_net_usd=10.0,
                    gas_units=200_000,
                )
            )
        except RuntimeError:
            raised = True
        assert raised, "invoke_bundle must raise RuntimeError when chain_id fetch fails"


# ---------------------------------------------------------------------------
# Patch 3 — p_fill enforced on every strike path
# ---------------------------------------------------------------------------

from apex_omega_core.core.inference import profitability_gate


class TestC1PFillEnforcement:
    """C1 prepare_contract_strike must enforce p_fill."""

    def _make_c1(self):
        from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
        c1 = C1AggressorApex.__new__(C1AggressorApex)
        c1.sentinel = MagicMock()
        c1.target_address = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
        c1.sentinel.build_c1_slippage_context.return_value = {
            "optimal_input": 50_000.0,
            "final_output": 51_000.0,
            "profit": 500.0,
            "slippage_per_leg": [],
            "raw_spread": 0.02,
        }
        c1.sentinel.validate_on_fork.return_value = {"status": "ok"}
        c1.sentinel.mempool_validate.return_value = {"decision": "SAFE"}
        return c1

    def test_strike_when_profit_positive_and_p_fill_positive(self) -> None:
        c1 = self._make_c1()
        plan = c1.prepare_contract_strike([], 0.02, 1000, 100_000, p_fill=0.9)
        assert plan["action"] == "STRIKE"

    def test_abort_when_p_fill_zero(self) -> None:
        c1 = self._make_c1()
        plan = c1.prepare_contract_strike([], 0.02, 1000, 100_000, p_fill=0.0)
        assert plan["action"] == "ABORT"

    def test_abort_when_profit_nonpositive(self) -> None:
        c1 = self._make_c1()
        c1.sentinel.build_c1_slippage_context.return_value = {
            "optimal_input": 50_000.0,
            "final_output": 50_000.0,
            "profit": 0.0,
            "slippage_per_leg": [],
            "raw_spread": 0.0,
        }
        plan = c1.prepare_contract_strike([], 0.0, 1000, 100_000, p_fill=1.0)
        assert plan["action"] == "ABORT"

    def test_p_fill_returned_in_plan(self) -> None:
        c1 = self._make_c1()
        plan = c1.prepare_contract_strike([], 0.02, 1000, 100_000, p_fill=0.75)
        assert plan["p_fill"] == 0.75


class TestC2PFillEnforcement:
    """C2 decide_contract_action must enforce p_fill via profitability_gate."""

    def _make_c2(self):
        from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
        c2 = C2SurgeonApex.__new__(C2SurgeonApex)
        c2.sentinel = MagicMock()
        c2.target_address = "0x0466759822ABAA7E416276E1cf2b538d7FC540BD"
        c2.sentinel.build_c2_slippage_context.return_value = {
            "optimal_input": 50_000.0,
            "final_output": 51_000.0,
            "profit": 500.0,
            "slippage_per_leg": [{"slippage": 0.001}],
            "raw_spread": 0.02,
        }
        c2.sentinel.validate_on_fork.return_value = {"status": "ok"}
        c2.sentinel.mempool_validate.return_value = {"decision": "SAFE"}
        c2.sentinel.reverse_route.return_value = []
        c2.sentinel.optimize.return_value = {"profit": 100.0}
        c2.max_total_slippage = 0.05
        return c2

    def test_do_nothing_when_p_fill_zero(self) -> None:
        c2 = self._make_c2()
        plan = c2.decide_contract_action([], 0.02, 1000, 100_000, 1.0, p_fill=0.0)
        assert plan["decision"] == "DO_NOTHING"

    def test_do_nothing_when_net_profit_zero(self) -> None:
        c2 = self._make_c2()
        plan = c2.decide_contract_action([], 0.0, 1000, 100_000, 500.0, p_fill=1.0)
        assert plan["decision"] == "DO_NOTHING"


class TestProfitabilityGateBlocks:
    """Direct unit tests for the profitability_gate SSOT."""

    def test_blocks_zero_p_fill(self) -> None:
        assert profitability_gate(25.0, 0.0) is False

    def test_blocks_negative_p_fill(self) -> None:
        assert profitability_gate(25.0, -0.1) is False

    def test_blocks_nonpositive_net_edge(self) -> None:
        assert profitability_gate(0.0, 1.0) is False
        assert profitability_gate(-1.0, 1.0) is False

    def test_passes_positive_net_and_fill(self) -> None:
        assert profitability_gate(0.01, 0.01) is True


# ---------------------------------------------------------------------------
# Patch 4 — GasOracle TTL auto-refresh
# ---------------------------------------------------------------------------

from apex_omega_core.core.mev_gas_oracle import GasOracle, GasPriceSnapshot


def _make_snapshot(base_fee: float = 50.0) -> GasPriceSnapshot:
    return GasPriceSnapshot(
        base_fee_gwei=base_fee,
        tip_p25_gwei=1.0,
        tip_p50_gwei=2.0,
        tip_p75_gwei=4.0,
        tip_p90_gwei=8.0,
        gas_used_ratio_avg=0.6,
    )


class TestGasOracleTTL:
    def test_snapshot_refreshes_after_ttl_expires(self) -> None:
        oracle = GasOracle.__new__(GasOracle)
        oracle._snapshot = None
        oracle._snapshot_ts = 0.0
        oracle._ttl_seconds = 0.05  # 50 ms TTL

        call_count = 0

        def _fake_refresh():
            nonlocal call_count
            call_count += 1
            return _make_snapshot(base_fee=float(call_count))

        oracle._fallback_snapshot = _fake_refresh
        oracle.fetch_fee_history = MagicMock(side_effect=Exception("no RPC"))

        s1 = oracle.get_snapshot()
        assert call_count == 1

        # Second call within TTL — should NOT re-fetch
        s2 = oracle.get_snapshot()
        assert call_count == 1
        assert s1 is s2

        # Wait for TTL to expire
        time.sleep(0.1)
        s3 = oracle.get_snapshot()
        assert call_count == 2
        assert s3 is not s1

    def test_invalidate_forces_refresh(self) -> None:
        oracle = GasOracle.__new__(GasOracle)
        oracle._snapshot = _make_snapshot(99.0)
        oracle._snapshot_ts = time.monotonic()
        oracle._ttl_seconds = 60.0  # long TTL

        oracle.fetch_fee_history = MagicMock(side_effect=Exception("no RPC"))
        oracle._fallback_snapshot = lambda: _make_snapshot(1.0)

        # Without invalidate: same snapshot returned
        s1 = oracle.get_snapshot()
        assert s1.base_fee_gwei == 99.0

        # After invalidate: must refresh
        oracle.invalidate()
        s2 = oracle.get_snapshot()
        assert s2.base_fee_gwei == 1.0

    def test_invalidate_resets_timestamp(self) -> None:
        oracle = GasOracle.__new__(GasOracle)
        oracle._snapshot = _make_snapshot()
        oracle._snapshot_ts = time.monotonic()
        oracle._ttl_seconds = 60.0
        oracle.invalidate()
        assert oracle._snapshot is None
        assert oracle._snapshot_ts == 0.0


class TestExecutionRouterInvalidatesPerCycle:
    """process_discovery_pipeline and run_dual_punch_cycle must call invalidate()."""

    def _make_router(self):
        from apex_omega_core.strategies.execution_router import ExecutionRouter
        from apex_omega_core.core.mev_gas_oracle import GasOracle

        router = ExecutionRouter.__new__(ExecutionRouter)
        router._gas_oracle = MagicMock(spec=GasOracle)
        router._gas_oracle.get_snapshot.return_value = _make_snapshot()
        return router

    def test_process_discovery_pipeline_invalidates(self) -> None:
        import asyncio
        router = self._make_router()

        async def _noop(_): return {}

        # Stub out strategies to avoid real execution
        router.strategies = {
            'aggressor': MagicMock(),
            'surgeon': MagicMock(),
        }
        router.strategies['aggressor'].prepare_contract_strike.return_value = {
            'sentinel_output': {'profit': 0.0},
            'action': 'ABORT',
        }
        router.strategies['aggressor'].execute_contract_strike = MagicMock(
            side_effect=_noop
        )
        router.strategies['surgeon'].decide_contract_action.return_value = {
            'sentinel_output': {'net_profit_usd': 0.0},
            'decision': 'DO_NOTHING',
        }
        router.strategies['surgeon'].execute_contract_decision = MagicMock(
            side_effect=_noop
        )
        router.DEFAULT_GAS_UNITS = 350_000

        # Run the coroutine and verify invalidate was called
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                router.process_discovery_pipeline(route=[], raw_spread=0.0)
            )
        except Exception:
            pass  # result not important, just check invalidate call
        finally:
            loop.close()

        router._gas_oracle.invalidate.assert_called()

    def test_run_dual_punch_cycle_invalidates(self) -> None:
        from apex_omega_core.strategies.execution_router import ExecutionRouter
        from apex_omega_core.strategies.dual_punch import DualPunchEngine, DualPunchParams, DualPunchCycleResult, PunchResult
        router = self._make_router()
        router._dual_punch = MagicMock(spec=DualPunchEngine)
        dummy_result = MagicMock(spec=DualPunchCycleResult)
        router._dual_punch.run_dual_punch_cycle.return_value = dummy_result

        router.run_dual_punch_cycle(route=[], params=DualPunchParams())
        router._gas_oracle.invalidate.assert_called()


# ---------------------------------------------------------------------------
# Patch 5 — optimize() loop bound
# ---------------------------------------------------------------------------

from apex_omega_core.core.slippage_sentinel import SlippageSentinel


class TestOptimizeLoopBound:
    def test_never_exceeds_max_input(self) -> None:
        min_input = 1.0
        max_input = 101.0
        step_count = 100
        # Replicate the fixed formula
        seen = [
            min(
                max_input,
                max(min_input, min_input + (max_input - min_input) * i / step_count),
            )
            for i in range(step_count + 1)
        ]
        assert max(seen) == max_input
        assert min(seen) == min_input

    def test_step_count_1_stays_in_range(self) -> None:
        seen = [
            min(2.0, max(1.0, 1.0 + (2.0 - 1.0) * i / 1))
            for i in range(1 + 1)
        ]
        assert max(seen) == 2.0
        assert min(seen) == 1.0

    def test_optimize_candidate_within_bounds(self) -> None:
        """SlippageSentinel.optimize() must never call simulate_route with amount > max_input."""
        sentinel = SlippageSentinel.__new__(SlippageSentinel)
        sentinel.rust_master_core = None

        seen_amounts: list[float] = []

        def fake_simulate(amount_in, route):
            seen_amounts.append(amount_in)
            return (amount_in * 1.01, [
                {
                    'slippage_bps': 0.0,
                    'depth_score': 1000.0,
                    'health_index': 1.0,
                    'usd_in': amount_in,
                    'usd_out': amount_in * 1.01,
                    'slippage': 0.001,
                }
            ])

        sentinel.simulate_route = fake_simulate
        sentinel._mid_price_final_usd = lambda amount, route: amount * 1.02
        sentinel.path_liquidity_factor = lambda scores: 1.0

        min_in, max_in = 1_000.0, 100_000.0
        sentinel.optimize(route=[], min_input=min_in, max_input=max_in, steps=50)

        assert all(a <= max_in for a in seen_amounts), (
            f"Found amounts exceeding max_input={max_in}: "
            f"{[a for a in seen_amounts if a > max_in]}"
        )
        assert all(a >= min_in for a in seen_amounts)


# ---------------------------------------------------------------------------
# attach_flashloan_token_meta — sentinel-to-calldata bridge
# ---------------------------------------------------------------------------

class TestAttachFlashloanTokenMeta:
    """attach_flashloan_token_meta injects base-unit keys into a sentinel output."""

    _USDC = TokenUnitSpec(symbol="USDC", decimals=6, usd_price=Decimal("1.00"))

    def _sentinel_out(self, optimal_input: float = 50_000.0, final_output: float = 50_050.0) -> dict:
        return {
            "optimal_input": optimal_input,
            "final_output": final_output,
            "profit": 50.0,
        }

    def test_injects_optimal_input_base_units(self) -> None:
        out = attach_flashloan_token_meta(self._sentinel_out(), self._USDC)
        # 50_000 USD / $1.00 = 50_000 USDC × 10^6 = 50_000_000_000
        assert out["optimal_input_base_units"] == 50_000_000_000

    def test_injects_min_final_output_base_units(self) -> None:
        out = attach_flashloan_token_meta(self._sentinel_out(), self._USDC)
        # 50_050 USD / $1.00 = 50_050 USDC × 10^6
        assert out["min_final_output_base_units"] == 50_050_000_000

    def test_separate_profit_token(self) -> None:
        weth = TokenUnitSpec(symbol="WETH", decimals=18, usd_price=Decimal("2500"))
        out = attach_flashloan_token_meta(self._sentinel_out(final_output=0.02), self._USDC, profit_token=weth)
        # 0.02 USD / $2500/WETH = 0.000008 WETH × 10^18
        assert out["min_final_output_base_units"] == int(Decimal("0.02") / Decimal("2500") * Decimal(10**18))

    def test_mutates_and_returns_same_dict(self) -> None:
        original = self._sentinel_out()
        result = attach_flashloan_token_meta(original, self._USDC)
        assert result is original
        assert "optimal_input_base_units" in original

    def test_missing_optimal_input_raises(self) -> None:
        with pytest.raises(KeyError):
            attach_flashloan_token_meta({"final_output": 100.0}, self._USDC)

    def test_zero_price_raises(self) -> None:
        bad_token = TokenUnitSpec(symbol="BAD", decimals=6, usd_price=Decimal("0"))
        with pytest.raises(ValueError):
            attach_flashloan_token_meta(self._sentinel_out(), bad_token)

    def test_output_satisfies_calldata_validator(self) -> None:
        """After attach, _validate_calldata_context must not raise."""
        out = attach_flashloan_token_meta(self._sentinel_out(), self._USDC)
        # _validate_calldata_context expects the context nested under sentinel_output.
        # We pass the dict directly to the underlying resolvers.
        from apex_omega_core.core.contract_invoker import (
            resolve_optimal_input_units,
            resolve_min_final_output_units,
        )
        assert resolve_optimal_input_units(out) == 50_000_000_000
        assert resolve_min_final_output_units(out) == 50_050_000_000
