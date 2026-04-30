from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .multi_market_scanner import ScannerOpportunity, scan_multi_market
from .rpc_tester import get_canonical_two_leg_state
from .live_strategy_steps import LiveStrategyBuildResult, build_live_strategy_output_from_state


@dataclass(frozen=True)
class PipelineCandidate:
    opportunity: ScannerOpportunity
    build: LiveStrategyBuildResult | None
    reason: str


@dataclass(frozen=True)
class ScannerStrategyPipelineResult:
    scanned: int
    candidates: list[PipelineCandidate]


def run_scanner_strategy_pipeline(
    executor_address: str,
    max_pairs: int = 24,
    min_spread_bps: float = 10.0,
    max_candidates: int = 5,
    min_net_profit_usd: float = 1.0,
    gas_cost_usd: float = 0.55,
    flash_fee_bps: float = 5.0,
    risk_buffer_usd: float = 0.0,
) -> ScannerStrategyPipelineResult:
    ops = scan_multi_market(max_pairs=max_pairs, min_spread_bps=min_spread_bps)
    candidates: list[PipelineCandidate] = []

    for op in ops[:max_candidates]:
        # Current executable builder is wired for canonical QSV2 -> UV3 USDCe/WMATIC.
        # Other scanner hits are surfaced but not auto-built until their venue encoders are enabled.
        canonical = (
            op.base_symbol == "USDCe"
            and op.quote_symbol == "WMATIC"
            and op.buy_venue == "quickswap_v2"
            and op.sell_venue == "uniswap_v3"
        )
        if not canonical:
            candidates.append(PipelineCandidate(op, None, "scanner hit not yet supported by route-step auto-builder"))
            continue

        state = get_canonical_two_leg_state()
        build = build_live_strategy_output_from_state(
            state,
            executor_address=executor_address,
            min_net_profit_usd=min_net_profit_usd,
            gas_cost_usd=gas_cost_usd,
            flash_fee_bps=flash_fee_bps,
            risk_buffer_usd=risk_buffer_usd,
        )
        candidates.append(PipelineCandidate(op, build, build.reason))

    return ScannerStrategyPipelineResult(scanned=len(ops), candidates=candidates)
