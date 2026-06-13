from __future__ import annotations

import time
from dataclasses import dataclass

from web3 import Web3

from .execution_compiler import ExecutionCompiler
from .live_strategy_steps import LiveStrategyBuildResult, build_live_strategy_output_from_state
from .multi_market_scanner import ScannerOpportunity, V2_PAIR_ABI, get_w3, scan_multi_market
from .polygon_market_registry import TOKENS, VENUES
from .rpc_tester import get_canonical_two_leg_state
from .swap_adapters import SwapRequest, UniversalSwapAdapter

_USD_STABLE_QUOTES = {"USDCe", "USDC", "USDT", "DAI"}


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


def _v2_reserves_for_direction(pool: str, token_in: str, token_out: str, dec_in: int, dec_out: int) -> tuple[float, float]:
    w3 = get_w3()
    pair = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=V2_PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    token0 = Web3.to_checksum_address(pair.functions.token0().call())
    token1 = Web3.to_checksum_address(pair.functions.token1().call())
    token_in_cs = Web3.to_checksum_address(token_in)
    token_out_cs = Web3.to_checksum_address(token_out)
    if token0 == token_in_cs and token1 == token_out_cs:
        return r0 / (10 ** dec_in), r1 / (10 ** dec_out)
    if token1 == token_in_cs and token0 == token_out_cs:
        return r1 / (10 ** dec_in), r0 / (10 ** dec_out)
    raise ValueError("pool token order does not match requested swap direction")


def _cpmm_out(amount_in: float, reserve_in: float, reserve_out: float, fee_bps: float) -> float:
    if amount_in <= 0.0 or reserve_in <= 0.0 or reserve_out <= 0.0:
        return 0.0
    amount_in_after_fee = amount_in * (1.0 - float(fee_bps) / 10_000.0)
    return (reserve_out * amount_in_after_fee) / (reserve_in + amount_in_after_fee)


def _build_v2_dynamic_candidate(
    op: ScannerOpportunity,
    executor_address: str,
    min_net_profit_usd: float,
    gas_cost_usd: float,
    flash_fee_bps: float,
    risk_buffer_usd: float,
    minout_buffer_bps: float = 25.0,
) -> LiveStrategyBuildResult:
    receiver = Web3.to_checksum_address(executor_address)
    buy_venue = VENUES[op.buy_venue]
    sell_venue = VENUES[op.sell_venue]
    if buy_venue.kind != "v2" or sell_venue.kind != "v2":
        return LiveStrategyBuildResult(False, "dynamic builder only handles V2->V2 here", None)
    if op.quote_symbol not in _USD_STABLE_QUOTES:
        return LiveStrategyBuildResult(False, "dynamic V2 builder requires USD-stable quote token for owner-net accounting", None)

    base = TOKENS[op.base_symbol]
    quote = TOKENS[op.quote_symbol]

    try:
        buy_r_in, buy_r_out = _v2_reserves_for_direction(
            op.buy_pool,
            quote.address,
            base.address,
            quote.decimals,
            base.decimals,
        )
        sell_r_in, sell_r_out = _v2_reserves_for_direction(
            op.sell_pool,
            base.address,
            quote.address,
            base.decimals,
            quote.decimals,
        )
    except Exception as exc:  # noqa: BLE001
        return LiveStrategyBuildResult(False, "dynamic V2 reserves unavailable", None, diagnostics={"error": str(exc)})

    max_quote_in = min(100.0, buy_r_in * 0.15, sell_r_out * 0.15)
    if max_quote_in <= 0.0:
        return LiveStrategyBuildResult(False, "dynamic V2 reserves cannot support positive input", None)

    amount_quote_in = max_quote_in
    amount_base_out = _cpmm_out(amount_quote_in, buy_r_in, buy_r_out, buy_venue.default_fee_bps)
    max_base_into_sell = sell_r_in * 0.15
    if amount_base_out > max_base_into_sell and amount_base_out > 0.0:
        amount_quote_in *= max_base_into_sell / amount_base_out * 0.95
        amount_base_out = _cpmm_out(amount_quote_in, buy_r_in, buy_r_out, buy_venue.default_fee_bps)
    final_quote_out = _cpmm_out(amount_base_out, sell_r_in, sell_r_out, sell_venue.default_fee_bps)
    gross_profit = final_quote_out - amount_quote_in
    flash_fee = amount_quote_in * (flash_fee_bps / 10_000.0)
    net_profit = gross_profit - flash_fee - risk_buffer_usd
    owner_submission_edge = net_profit - gas_cost_usd

    if owner_submission_edge <= min_net_profit_usd:
        return LiveStrategyBuildResult(False, "V2 dynamic owner submission edge below threshold", None, diagnostics={
            "gross_profit": gross_profit,
            "net_profit": net_profit,
            "owner_submission_edge": owner_submission_edge,
            "notional": amount_quote_in,
            "buy_reserve_in": buy_r_in,
            "buy_reserve_out": buy_r_out,
            "sell_reserve_in": sell_r_in,
            "sell_reserve_out": sell_r_out,
        })

    adapter = UniversalSwapAdapter()
    deadline = int(time.time()) + 90
    amount_in_raw = _raw(amount_quote_in, quote.decimals)
    leg1_min_raw = _raw(amount_base_out * (1 - minout_buffer_bps / 10_000), base.decimals)
    leg2_in_raw = _raw(amount_base_out, base.decimals)
    leg2_min_raw = _raw(final_quote_out * (1 - minout_buffer_bps / 10_000), quote.decimals)

    steps = [
        adapter.build_step(SwapRequest(op.buy_venue, quote.address, base.address, amount_in_raw, leg1_min_raw, receiver, deadline)),
        adapter.build_step(SwapRequest(op.sell_venue, base.address, quote.address, leg2_in_raw, leg2_min_raw, receiver, deadline)),
    ]

    strategy_output = {
        "asset": quote.address,
        "executor_address": receiver,
        "flash_loan_receiver": receiver,
        "min_profit": _raw(max(net_profit, 0.000001), quote.decimals),
        "gas_reserve_asset": 0,
        "dex_fee_reserve_asset": 0,
        "steps": steps,
        "opportunity": {
            "net_profit_usd": net_profit,
            "owner_submission_edge_usd": owner_submission_edge,
            "gas_cost_usd": gas_cost_usd,
            "gross_profit": gross_profit,
            "amount_in": amount_quote_in,
            "leg1_out": amount_base_out,
            "leg2_out": final_quote_out,
        },
    }
    compiled = ExecutionCompiler().compile_for_institutional(strategy_output)
    return LiveStrategyBuildResult(True, "dynamic V2->V2 reserve-backed strategy wired", strategy_output, len(compiled.encoded_payload), compiled.min_profit, {
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "owner_submission_edge": owner_submission_edge,
        "gas_cost_usd": gas_cost_usd,
        "steps": len(steps),
        "notional": amount_quote_in,
        "buy_reserve_in": buy_r_in,
        "buy_reserve_out": buy_r_out,
        "sell_reserve_in": sell_r_in,
        "sell_reserve_out": sell_r_out,
    })


def run_scanner_strategy_pipeline(
    executor_address: str,
    max_pairs: int = 24,
    max_candidates: int = 5,
    min_net_profit_usd: float = 1.0,
    gas_cost_usd: float = 0.55,
    flash_fee_bps: float = 5.0,
    risk_buffer_usd: float = 0.0,
) -> ScannerStrategyPipelineResult:
    ops = scan_multi_market(max_pairs=max_pairs)
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
