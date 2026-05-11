from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from .execution_dna import live_execution_blockers
from .execution_engine import ExecutionEngine
from .mev_mempool_watcher import MempoolWatcher, MempoolStateSnapshot
from .runtime_config import RuntimeConfig, load_runtime_config
from .scanner_strategy_pipeline import run_scanner_strategy_pipeline


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _mempool_summary(state: MempoolStateSnapshot) -> dict[str, Any]:
    return {
        "pending_swap_count": int(state.pending_swap_count),
        "competing_bot_density": float(state.competing_bot_density),
        "reserve_delta_keys": len(state.reserve_delta_forecast),
        "snapshot_timestamp_ms": int(state.snapshot_timestamp_ms),
    }


async def run_live_e2e_cycle(
    *,
    submit_live: bool = False,
    capture_seconds: float = 1.5,
    max_candidates: int = 5,
    config: RuntimeConfig | None = None,
    watcher: MempoolWatcher | None = None,
) -> dict[str, Any]:
    cfg = config or load_runtime_config()
    blockers = live_execution_blockers(cfg)

    watcher_instance = watcher or MempoolWatcher(wss_url=cfg.polygon_wss)
    mempool_state = await watcher_instance.capture_snapshot(duration_s=capture_seconds)

    pipeline = run_scanner_strategy_pipeline(
        executor_address=cfg.c1_executor_address,
        max_candidates=max_candidates,
        min_net_profit_usd=cfg.min_net_profit_usd,
        gas_cost_usd=cfg.c1_gas_usd,
        flash_fee_bps=cfg.flash_loan_fee_bps,
        risk_buffer_usd=cfg.risk_buffer_usd,
    )

    strikeable = next(
        (
            candidate
            for candidate in pipeline.candidates
            if candidate.build
            and candidate.build.strikeable
            and candidate.build.strategy_output
        ),
        None,
    )
    if strikeable is None:
        return {
            "mode": "no_candidate",
            "submit_live": submit_live,
            "blockers": blockers,
            "scan": {"scanned": pipeline.scanned, "candidates": len(pipeline.candidates)},
            "mempool": _mempool_summary(mempool_state),
            "reason": "no strikeable scanner candidate with executable strategy output",
        }

    strategy_output_obj = strikeable.build.strategy_output
    if strategy_output_obj is None:
        return {
            "mode": "no_candidate",
            "submit_live": submit_live,
            "blockers": blockers,
            "scan": {"scanned": pipeline.scanned, "candidates": len(pipeline.candidates)},
            "mempool": _mempool_summary(mempool_state),
            "reason": "selected candidate had no strategy output",
        }

    if not isinstance(strategy_output_obj, dict):
        raise ValueError("selected strategy output must be a mapping")
    strategy_output = dict(strategy_output_obj)
    opportunity_raw = strategy_output.get("opportunity")
    if opportunity_raw is None:
        opportunity = {}
    elif isinstance(opportunity_raw, dict):
        opportunity = dict(opportunity_raw)
    else:
        raise ValueError("strategy opportunity must be a mapping when provided")
    opportunity.setdefault("net_profit_usd", 0.0)
    opportunity.setdefault("slippage_bps", 0.0)
    opportunity.setdefault("pool_tvl_usd", max(1.0, cfg.min_pool_tvl_usd))

    engine = ExecutionEngine(cfg)
    engine.validate_opportunity(opportunity)
    plan = engine.build_c1_plan(strategy_output)

    response: dict[str, Any] = {
        "mode": "simulate_only",
        "submit_live": submit_live,
        "blockers": blockers,
        "scan": {"scanned": pipeline.scanned, "candidates": len(pipeline.candidates)},
        "mempool": _mempool_summary(mempool_state),
        "selected": {
            "reason": strikeable.reason,
            "buy_venue": strikeable.opportunity.buy_venue,
            "sell_venue": strikeable.opportunity.sell_venue,
            "raw_spread_bps": float(strikeable.opportunity.raw_spread_bps),
        },
        "payload": {
            "compiled_payload_bytes": len(plan.compiled.encoded_payload),
            "calldata_bytes": len(plan.calldata),
            "flash_loan_amount": int(plan.flash_loan_amount),
            "min_profit": int(plan.compiled.min_profit),
            "asset": plan.compiled.asset,
            "target": plan.target,
        },
        "submission": {"attempted": False, "results": []},
    }

    if not submit_live:
        response["simulation"] = engine.simulate_only(plan)
        return response

    if blockers:
        response["mode"] = "blocked"
        response["submission"] = {
            "attempted": False,
            "results": [],
            "error": "live execution blocked by runtime safety gates",
        }
        return response

    raw_tx = engine.sign_transaction(plan)
    submit_results = engine.execute_bundle(raw_tx)
    response["mode"] = "submitted"
    response["submission"] = {
        "attempted": True,
        "raw_tx_len": len(raw_tx),
        "results": _serialize(submit_results),
    }
    return response
