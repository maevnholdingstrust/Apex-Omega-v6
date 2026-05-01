from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from .execution_compiler import ExecutionCompiler
from .polygon_market_registry import TOKENS
from .route_step_encoder import (
    build_quickswap_v2_step,
    build_uniswap_v3_step,
    validate_route_steps,
)
from .slippage_sentinel import SlippageSentinel


@dataclass(frozen=True)
class LiveStrategyBuildResult:
    strikeable: bool
    reason: str
    strategy_output: dict[str, Any] | None
    compiled_payload_len: int = 0
    min_profit: int = 0
    diagnostics: dict[str, Any] | None = None


def _to_raw(amount: float, decimals: int) -> int:
    return max(0, int(amount * (10 ** decimals)))


def _min_out(amount: float, decimals: int, buffer_bps: float) -> int:
    safe = amount * (1.0 - buffer_bps / 10_000.0)
    return max(1, int(safe * (10 ** decimals)))


def build_live_strategy_output_from_state(
    state: Mapping[str, Any],
    executor_address: str,
    min_net_profit_usd: float = 1.0,
    minout_buffer_bps: float = 25.0,
    gas_cost_usd: float = 0.55,
    flash_fee_bps: float = 9.0,
    risk_buffer_usd: float = 0.0,
) -> LiveStrategyBuildResult:
    sentinel = SlippageSentinel()

    fee1 = float(state["fee1"])
    r1_in = float(state["r1_in"])
    r1_out = float(state["r1_out"])
    fee2 = float(state["fee2"])
    r2_in = float(state["r2_in"])
    r2_out = float(state["r2_out"])

    amount_in = sentinel.optimal_two_leg_input(r1_in, r1_out, fee1, r2_in, r2_out, fee2)
    if amount_in <= 0:
        return LiveStrategyBuildResult(False, "optimizer returned zero amount", None, diagnostics={"amount_in": amount_in})

    flash_fee = amount_in * (flash_fee_bps / 10_000.0)
    result = sentinel.two_leg_arb_profit(
        amount_in,
        fee1, r1_in, r1_out,
        fee2, r2_in, r2_out,
        c_gas=0.0,
        c_loan=flash_fee,
        c_other=risk_buffer_usd,
    )
    net_profit = float(result["p_net"])
    owner_submission_edge = net_profit - gas_cost_usd
    if net_profit <= min_net_profit_usd:
        return LiveStrategyBuildResult(False, "net profit below threshold", None, diagnostics={"amount_in": amount_in, "net_profit": net_profit})

    usdc = TOKENS["USDCe"]
    wmatic = TOKENS["WMATIC"]
    deadline = int(time.time()) + 90

    amount_in_raw = _to_raw(amount_in, usdc.decimals)
    leg1_out_raw_min = _min_out(float(result["b_out_1"]), wmatic.decimals, minout_buffer_bps)
    leg2_in_raw = _to_raw(float(result["b_out_1"]), wmatic.decimals)
    leg2_out_raw_min = _min_out(float(result["a_out_2"]), usdc.decimals, minout_buffer_bps)

    steps = [
        build_quickswap_v2_step(
            token_in=usdc.address,
            token_out=wmatic.address,
            amount_in=amount_in_raw,
            min_amount_out=leg1_out_raw_min,
            recipient=executor_address,
            deadline=deadline,
            fee_bps=30,
        ),
        build_uniswap_v3_step(
            token_in=wmatic.address,
            token_out=usdc.address,
            amount_in=leg2_in_raw,
            min_amount_out=leg2_out_raw_min,
            recipient=executor_address,
            deadline=deadline,
            fee=500,
        ),
    ]
    validate_route_steps(steps)

    strategy_output = {
        "asset": usdc.address,
        "min_profit": _to_raw(max(0.000001, net_profit), usdc.decimals),
        "gas_reserve_asset": 0,
        "dex_fee_reserve_asset": 0,
        "steps": steps,
        "opportunity": {
            "net_profit_usd": net_profit,
            "owner_submission_edge_usd": owner_submission_edge,
            "gas_cost_usd": gas_cost_usd,
            "amount_in": amount_in,
            "leg1_out": float(result["b_out_1"]),
            "leg2_out": float(result["a_out_2"]),
            "gross_profit": float(result["p_gross"]),
        },
    }

    compiled = ExecutionCompiler().compile_for_institutional(strategy_output)
    return LiveStrategyBuildResult(
        True,
        "strategy output wired to executable route steps",
        strategy_output,
        compiled_payload_len=len(compiled.encoded_payload),
        min_profit=compiled.min_profit,
        diagnostics={
            "amount_in": amount_in,
            "leg1_out": float(result["b_out_1"]),
            "leg2_out": float(result["a_out_2"]),
            "net_profit": net_profit,
            "owner_submission_edge": owner_submission_edge,
            "gas_cost_usd": gas_cost_usd,
            "steps": len(steps),
        },
    )
