"""Unit tests for apex_omega_core.core.deterministic_slippage.

Validates the public functions including calculate_real_profit.
"""

import pytest
from apex_omega_core.core.deterministic_slippage import (
    calculate_deterministic_slippage_bps,
    calculate_cpmm_output_slippage_bps,
    calculate_real_profit,
    max_leg_slippage_bps,
)
import apex_omega_core.core as core_pkg


class TestCoreExports:

    def test_calculate_real_profit_exported(self):
        assert hasattr(core_pkg, "calculate_real_profit")


class TestCalculateRealProfit:

    def test_profit_positive(self):
        p = calculate_real_profit(110.0, 100.0, gas_cost_usd=1.0)
        assert p == 9.0

    def test_profit_negative(self):
        p = calculate_real_profit(100.0, 100.0, gas_cost_usd=5.0)
        assert p == -5.0
