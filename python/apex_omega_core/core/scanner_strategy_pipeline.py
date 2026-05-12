from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .execution_compiler import ExecutionCompiler
from .live_strategy_steps import LiveStrategyBuildResult, build_live_strategy_output_from_state
from .multi_market_scanner import ScannerOpportunity, scan_multi_market
from .polygon_market_registry import TOKENS, VENUES
from .rpc_tester import get_canonical_two_leg_state
from .swap_adapters import SwapRequest, UniversalSwapAdapter


@dataclass(frozen=True)
class PipelineCandidate:
    opportunity: ScannerOpportunity
    build: LiveStrategyBuildResult | None
    reason: str


@dataclass(frozen=True)
class ScannerStrategyPipelineResult:
    scanned: int
    candidates: list[PipelineCandidate]


def _raw(amount: float, decimals: int) -> int:
    return max(1, int(amount * (10 ** decimals)))


def _token_price_usd(symbol: str) -> float:
    stable = {"USDCe", "USDC", "USDT", "DAI"}
    if symbol in stable:
        return 1.0

    env_overrides = {
        "WMATIC": "APEX_POL_USD",
        "WETH": "APEX_ETH_USD",
        "WBTC": "APEX_BTC_USD",
    }
    env_key = env_overrides.get(symbol, f"APEX_{symbol}_USD")
    fallback = {
        "WMATIC": "0.85",
        "WETH": "3500",
        "WBTC": "65000",
    }.get(symbol, "0")
    price = float(os.getenv(env_key, fallback))
    if price <= 0:
        raise ValueError(f"missing positive USD price for token {symbol!r}")
    return price


def _raw_from_usd(amount_usd: float, token_symbol: str) -> int:
    token = TOKENS[token_symbol]
    token_price_usd = _token_price_usd(token_symbol)
    token_amount = float(amount_usd) / token_price_usd
    return max(1, int(token_amount * (10 ** token.decimals)))


def _build_v2_dynamic_candidate(
    op: ScannerOpportunity,
    executor_address: str,
    min_net_profit_usd: float,
    gas_cost_usd: float,
    flash_fee_bps: float,
    risk_buffer_usd: float,
    minout_buffer_bps: float = 25.0,
) -> LiveStrategyBuildResult:
    buy_venue = VENUES[op.buy_venue]
    sell_venue = VENUES[op.sell_venue]
    if buy_venue.kind != "v2" or sell_venue.kind != "v2":
        return LiveStrategyBuildResult(False, "dynamic builder only handles V2->V2 here", None)

    base = TOKENS[op.base_symbol]
    quote = TOKENS[op.quote_symbol]
    try:
        quote_price_usd = _token_price_usd(op.quote_symbol)
        base_price_usd = _token_price_usd(op.base_symbol)
    except ValueError as exc:
        return LiveStrategyBuildResult(False, str(exc), None)

    # Conservative sizing: start with $100 notional and convert to borrowed-token units.
    loan_amount_usd = 100.0
    amount_quote_in = loan_amount_usd / quote_price_usd
    amount_base_out = amount_quote_in / op.buy_price
    final_quote_out = amount_base_out * op.sell_price
    gross_profit = (final_quote_out - amount_quote_in) * quote_price_usd
    flash_fee = loan_amount_usd * (flash_fee_bps / 10_000.0)
    net_profit = gross_profit - flash_fee - risk_buffer_usd
    owner_submission_edge = net_profit - gas_cost_usd

    if net_profit <= min_net_profit_usd:
        return LiveStrategyBuildResult(False, "V2 dynamic net profit below threshold", None, diagnostics={"gross_profit": gross_profit, "net_profit": net_profit, "notional_usd": loan_amount_usd})

    adapter = UniversalSwapAdapter()
    deadline = int(time.time()) + 90
    amount_in_raw = _raw_from_usd(loan_amount_usd, op.quote_symbol)
    leg1_expected_out_usd = amount_base_out * base_price_usd
    leg1_min_raw = _raw_from_usd(
        leg1_expected_out_usd * (1 - minout_buffer_bps / 10_000),
        op.base_symbol,
    )
    leg2_in_raw = _raw(amount_base_out, base.decimals)
    leg2_expected_out_usd = final_quote_out * quote_price_usd
    leg2_min_raw = _raw_from_usd(
        leg2_expected_out_usd * (1 - minout_buffer_bps / 10_000),
        op.quote_symbol,
    )
    min_profit_raw = _raw_from_usd(max(net_profit, 0.000001), op.quote_symbol)

    steps = [
        adapter.build_step(SwapRequest(op.buy_venue, quote.address, base.address, amount_in_raw, leg1_min_raw, executor_address, deadline)),
        adapter.build_step(SwapRequest(op.sell_venue, base.address, quote.address, leg2_in_raw, leg2_min_raw, executor_address, deadline)),
    ]

    strategy_output = {
        "asset": quote.address,
        "min_profit": min_profit_raw,
        "flash_loan_amount_raw": amount_in_raw,
        "gas_reserve_asset": 0,
        "dex_fee_reserve_asset": 0,
        "steps": steps,
        "opportunity": {
            "loan_amount_usd": loan_amount_usd,
            "loan_amount_raw": amount_in_raw,
            "net_profit_usd": net_profit,
            "net_profit_raw": min_profit_raw,
            "owner_submission_edge_usd": owner_submission_edge,
            "gas_cost_usd": gas_cost_usd,
            "gross_profit": gross_profit,
            "amount_in": amount_quote_in,
            "leg1_out": amount_base_out,
            "leg2_out": final_quote_out,
            "token_prices_usd": {
                op.quote_symbol: quote_price_usd,
                op.base_symbol: base_price_usd,
            },
            "legs": [
                {
                    "venue": op.buy_venue,
                    "amount_in_raw": amount_in_raw,
                    "expected_out": amount_base_out,
                    "expected_out_usd": leg1_expected_out_usd,
                    "amount_out_min_raw": leg1_min_raw,
                    "token_in": op.quote_symbol,
                    "token_out": op.base_symbol,
                },
                {
                    "venue": op.sell_venue,
                    "amount_in_raw": leg2_in_raw,
                    "expected_out": final_quote_out,
                    "expected_out_usd": leg2_expected_out_usd,
                    "amount_out_min_raw": leg2_min_raw,
                    "token_in": op.base_symbol,
                    "token_out": op.quote_symbol,
                },
            ],
        },
    }
    compiled = ExecutionCompiler().compile_for_institutional(strategy_output)
    return LiveStrategyBuildResult(True, "dynamic V2->V2 strategy wired", strategy_output, len(compiled.encoded_payload), compiled.min_profit, {"gross_profit": gross_profit, "net_profit": net_profit, "owner_submission_edge": owner_submission_edge, "gas_cost_usd": gas_cost_usd, "steps": len(steps), "notional": amount_quote_in})


def run_scanner_strategy_pipeline(
    executor_address: str,
    max_pairs: int = 24,
    min_spread_bps: float = 10.0,
    max_candidates: int = 5,
    min_net_profit_usd: float = 1.0,
    gas_cost_usd: float = 0.55,
    flash_fee_bps: float = 9.0,
    risk_buffer_usd: float = 0.0,
) -> ScannerStrategyPipelineResult:
    ops = scan_multi_market(max_pairs=max_pairs, min_spread_bps=min_spread_bps)
    candidates: list[PipelineCandidate] = []

    for op in ops[:max_candidates]:
        canonical = op.base_symbol == "USDCe" and op.quote_symbol == "WMATIC" and op.buy_venue == "quickswap_v2" and op.sell_venue == "uniswap_v3"
        if canonical:
            state = get_canonical_two_leg_state()
            build = build_live_strategy_output_from_state(state, executor_address=executor_address, min_net_profit_usd=min_net_profit_usd, gas_cost_usd=gas_cost_usd, flash_fee_bps=flash_fee_bps, risk_buffer_usd=risk_buffer_usd)
            candidates.append(PipelineCandidate(op, build, build.reason))
            continue

        if VENUES[op.buy_venue].kind == "v2" and VENUES[op.sell_venue].kind == "v2":
            build = _build_v2_dynamic_candidate(op, executor_address, min_net_profit_usd, gas_cost_usd, flash_fee_bps, risk_buffer_usd)
            candidates.append(PipelineCandidate(op, build, build.reason))
            continue

        candidates.append(PipelineCandidate(op, None, "scanner hit not yet supported by dynamic route-step builder"))

    return ScannerStrategyPipelineResult(scanned=len(ops), candidates=candidates)
