"""Standalone constant-product AMM math for the Dual Punch SSOT pipeline.

This module implements the spec-locked two-leg arbitrage math without any
dependency on apex_omega_core.  It is intentionally self-contained so the
ssot_pipeline package can be imported and tested in isolation.

The AMM formula and 5-phase model are identical to those in
``apex_omega_core.core.slippage_sentinel.SlippageSentinel.two_leg_arb_profit``.
"""
from __future__ import annotations

from typing import Dict


def amm_swap(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Constant-product AMM swap with fee; slippage is embedded in the output.

    Parameters
    ----------
    amount_in:
        Input amount in the input token.
    reserve_in:
        Pool reserve of the input token.
    reserve_out:
        Pool reserve of the output token.
    fee:
        DEX fee rate as a decimal (e.g. 0.003 for 0.3%).

    Returns
    -------
    float
        Output amount.  Returns ``0.0`` when any input is non-positive.
    """
    amount_in_with_fee = amount_in * (1.0 - fee)
    if reserve_in <= 0.0 or reserve_out <= 0.0 or amount_in_with_fee <= 0.0:
        return 0.0
    return (amount_in_with_fee * reserve_out) / (reserve_in + amount_in_with_fee)


def two_leg_arb_profit(
    a_in: float,
    fee1: float,
    r1_in: float,
    r1_out: float,
    fee2: float,
    r2_in: float,
    r2_out: float,
    c_gas: float = 0.0,
    c_loan: float = 0.0,
    c_other: float = 0.0,
) -> Dict[str, float]:
    """Canonical two-swap arbitrage profit using constant-product AMM math.

    Implements the spec-locked 5-phase two-swap form:

    Phase A — starting inventory
        Start with ``a_in`` units of asset A.

    Phase B — buy-side swap (Swap 1: A → B)
        A_eff_1 = a_in  * (1 − fee1)
        B_out_1 = (A_eff_1 * r1_out) / (r1_in + A_eff_1)

    Phase C — inventory handoff
        b_out_1 is the *full* input to Swap 2 (no manual slippage subtraction).

    Phase D — sell-side swap (Swap 2: B → A)
        B_eff   = b_out_1 * (1 − fee2)
        a_out_2 = (B_eff * r2_out) / (r2_in + B_eff)

    Phase E — same-unit comparison
        p_gross = a_out_2 − a_in
        p_net   = a_out_2 − a_in − c_gas − c_loan − c_other

    Parameters
    ----------
    a_in:
        Starting amount of asset A.
    fee1:
        DEX fee rate for Swap 1 (decimal, e.g. 0.003 for 0.3%).
    r1_in, r1_out:
        Pool 1 reserves (asset A side, asset B side).
    fee2:
        DEX fee rate for Swap 2 (decimal).
    r2_in, r2_out:
        Pool 2 reserves (asset B side, asset A side).
    c_gas:
        Gas cost in asset-A units (default 0).
    c_loan:
        Flash-loan cost in asset-A units (default 0).
    c_other:
        Any other cost in asset-A units (default 0).

    Returns
    -------
    dict with keys:
        b_out_1 – Swap 1 output (asset B); becomes Swap 2 input
        a_out_2 – Swap 2 output (asset A); final inventory
        p_gross – gross profit in asset A = a_out_2 − a_in
        p_net   – net profit  in asset A = p_gross − c_gas − c_loan − c_other
    """
    b_out_1 = amm_swap(float(a_in), float(r1_in), float(r1_out), float(fee1))
    a_out_2 = amm_swap(b_out_1, float(r2_in), float(r2_out), float(fee2))
    p_gross = a_out_2 - float(a_in)
    p_net = p_gross - float(c_gas) - float(c_loan) - float(c_other)
    return {
        "b_out_1": b_out_1,
        "a_out_2": a_out_2,
        "p_gross": p_gross,
        "p_net": p_net,
    }
