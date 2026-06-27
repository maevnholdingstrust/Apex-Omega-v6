#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

LOG_DIR = ROOT / "logs"
STATUS_PATH = LOG_DIR / "autonomous_scanner_status.json"
EVENTS_PATH = LOG_DIR / "autonomous_scanner_events.jsonl"
PID_PATH = LOG_DIR / "autonomous_scanner.pid"
CSV_PATH = ROOT / "dry_run_results.csv"
PRICE_REPORT_PATH = ROOT / "runtime" / "price_discovery_report.json"
LIVE_QUOTE_DIAGNOSTICS_PATH = ROOT / "runtime" / "live_quote_near_misses.json"

_STOP = False


def _handle_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
    global _STOP
    _STOP = True


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _market_data_policy() -> dict[str, Any]:
    real_only = _env_bool("REAL_MARKET_DATA_ONLY", True)
    synthetic_allowed = _env_bool("APEX_ALLOW_SYNTHETIC_TEST_DATA", False)
    return {
        "real_market_data_only": real_only,
        "synthetic_test_data_allowed": synthetic_allowed,
        "status": "VIOLATION" if real_only and synthetic_allowed else "PASS",
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _append_event(payload: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False) + "\n")


def _price_coverage_status() -> dict[str, Any]:
    if not PRICE_REPORT_PATH.exists():
        return {
            "status": "not_available",
            "report_path": str(PRICE_REPORT_PATH),
            "priced_count": 0,
            "discovered_count": 0,
            "unpriced_tokens": [],
            "quarantined_pool_count": 0,
            "quarantine_summary": {},
        }
    try:
        report = json.loads(PRICE_REPORT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "report_path": str(PRICE_REPORT_PATH),
            "error": str(exc),
            "priced_count": 0,
            "discovered_count": 0,
            "unpriced_tokens": [],
            "quarantined_pool_count": 0,
            "quarantine_summary": {},
        }
    discovered = report.get("discovered_tokens") or []
    priced = report.get("priced_tokens") or []
    unpriced = report.get("unpriced_tokens") or []
    quarantined = report.get("quarantined_pools") or []
    return {
        "status": "complete" if discovered else "empty",
        "report_path": str(PRICE_REPORT_PATH),
        "priced_count": len(priced),
        "discovered_count": len(discovered),
        "coverage_ratio": round(len(priced) / max(len(discovered), 1), 6),
        "unpriced_tokens": unpriced[:25],
        "quarantined_pool_count": len(quarantined),
        "quarantine_summary": report.get("quarantine_summary") or {},
        "quarantine_examples": quarantined[:10],
    }


def _live_quote_status() -> dict[str, Any]:
    if not LIVE_QUOTE_DIAGNOSTICS_PATH.exists():
        return {
            "status": "not_available",
            "report_path": str(LIVE_QUOTE_DIAGNOSTICS_PATH),
            "complete_quote_paths": 0,
            "profitable_records": 0,
            "top_reject_reason": "",
        }
    try:
        payload = json.loads(LIVE_QUOTE_DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "report_path": str(LIVE_QUOTE_DIAGNOSTICS_PATH),
            "error": str(exc),
            "complete_quote_paths": 0,
            "profitable_records": 0,
            "top_reject_reason": "",
        }
    near_misses = payload.get("top_near_misses") or []
    reasons: dict[str, int] = {}
    for item in near_misses:
        reason = str(item.get("reason") or "").strip()
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    top_reason = max(reasons, key=reasons.get) if reasons else ""
    return {
        "status": payload.get("status") or "unknown",
        "report_path": str(LIVE_QUOTE_DIAGNOSTICS_PATH),
        "complete_quote_paths": int(payload.get("complete_quote_paths") or 0),
        "profitable_records": int(payload.get("profitable_records") or 0),
        "cycles_evaluated": int(payload.get("cycles_evaluated") or 0),
        "quote_failure_summary": payload.get("quote_failure_summary") or {},
        "quote_failure_detail_count": len(payload.get("quote_failure_details") or []),
        "near_miss_count": len(near_misses),
        "top_reject_reason": top_reason,
        "reject_summary": dict(sorted(reasons.items())),
    }


def _proof_checkpoint(
    *,
    payload_validated_count: int,
    payload_candidate_count: int,
    payload_rejected_count: int,
    raw_profitable_count: int,
    live_quote: dict[str, Any],
) -> str:
    if payload_validated_count > 0:
        return "fork_simulation_ready"
    if payload_rejected_count > 0 or payload_candidate_count > 0:
        return "blocked_at_payload_validation"
    if int(live_quote.get("complete_quote_paths") or 0) <= 0:
        failures = live_quote.get("quote_failure_summary") or {}
        return "blocked_at_live_quote_adapter" if failures else "blocked_at_live_quote_path_completion"
    top_reason = str(live_quote.get("top_reject_reason") or "")
    if top_reason in {
        "BUY_LEG1_PRICE_NOT_BELOW_SELL_LEG2_PRICE",
        "BUY_LEG1_PRICE_INVALID",
        "SELL_LEG2_PRICE_INVALID",
    }:
        return "blocked_at_executable_price_invariant"
    if raw_profitable_count <= 0:
        return "blocked_at_no_owner_positive_route_after_repayment_gas"
    return "blocked_at_live_quote_or_payload_validation"


def _safe_route(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": row.get("pair"),
        "hop_count": row.get("hop_count"),
        "trade_size_usd": row.get("trade_size_usd"),
        "expected_net_edge": row.get("expected_net_edge"),
        "e_profit": row.get("e_profit"),
        "route_tokens": row.get("route_tokens"),
        "route_pools": row.get("route_pools"),
        "route_dexes": row.get("route_dexes"),
        "route_id": row.get("route_id"),
    }


def _parse_amounts(value: Any) -> list[float]:
    if not value:
        return []
    if isinstance(value, list):
        return [float(item) for item in value]
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [float(item) for item in loaded]


def _parse_bools(value: Any) -> list[bool]:
    if not value:
        return []
    if isinstance(value, list):
        return [bool(item) for item in value]
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [bool(item) for item in loaded]


def _parse_optional_strings(value: Any) -> list[str | None]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) if item else None for item in value]
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) if item else None for item in loaded]


def _parse_optional_pairs(value: Any) -> list[list[int] | None]:
    if not value:
        return []
    if isinstance(value, list):
        loaded = value
    else:
        try:
            loaded = json.loads(str(value))
        except json.JSONDecodeError:
            return []
    if not isinstance(loaded, list):
        return []
    out: list[list[int] | None] = []
    for item in loaded:
        if not item:
            out.append(None)
            continue
        if isinstance(item, list) and len(item) == 2:
            out.append([int(item[0]), int(item[1])])
        else:
            out.append(None)
    return out


def _route_to_cycle(row: dict[str, Any]):
    from apex_omega_core.core.route_graph import CycleRecord

    tokens = [item for item in str(row.get("route_tokens") or "").split("->") if item]
    pools = [item for item in str(row.get("route_pools") or "").split("->") if item]
    dexes = [item for item in str(row.get("route_dexes") or "").split("->") if item]
    amounts_in = _parse_amounts(row.get("route_leg_amounts_in"))
    amounts_out = _parse_amounts(row.get("route_leg_amounts_out"))
    hop_count = int(row.get("hop_count") or 0)
    if not (tokens and pools and dexes and amounts_in and amounts_out and hop_count >= 2):
        return None
    if len(tokens) != hop_count + 1 or not (len(pools) == len(dexes) == len(amounts_in) == len(amounts_out) == hop_count):
        return None
    cycle = CycleRecord(
        tokens=tokens,
        pools=pools,
        dexes=dexes,
        hop_count=hop_count,
        amount_in=float(amounts_in[0]),
        trade_size_usd=float(row.get("trade_size_usd") or 0.0),
        amount_out=float(amounts_out[-1]),
        gross_profit=0.0,
        gross_profit_usd=float(row.get("gross_profit_usd") or 0.0),
        flash_fee_usd=float(row.get("flash_fee_usd") or 0.0),
        gas_cost_usd=float(row.get("gas_cost_usd") or 0.0),
        net_profit_usd=float(row.get("expected_net_edge") or 0.0),
        p_fill=float(row.get("p_fill") or 0.0),
        e_profit=float(row.get("e_profit") or 0.0),
        profitable=bool(row.get("profitable")),
        swap_0_to_1=_parse_bools(row.get("route_swap_0_to_1")),
        leg_amounts_in=amounts_in,
        leg_amounts_out=amounts_out,
        curve_coin_indices=_parse_optional_pairs(row.get("route_curve_coin_indices")),
    )
    pool_ids = _parse_optional_strings(row.get("route_pool_ids"))
    if pool_ids:
        cycle.pool_ids = pool_ids
    return cycle


def _payload_ready_routes(rows: list[dict[str, Any]], cfg: Any) -> list[dict[str, Any]]:
    from apex_omega_core.core.execution_engine import ExecutionEngine
    from apex_omega_core.core.expanded_strategy_steps import build_expanded_strategy_output_from_cycle

    engine = ExecutionEngine(cfg)
    token_prices: dict[str, float] = {}
    # Route records already carry USD-denominated profit; derive minimum profit
    # conversion from the start token using a conservative route-local fallback.
    for row in rows:
        route_tokens = [item for item in str(row.get("route_tokens") or "").split("->") if item]
        if route_tokens and float(row.get("trade_size_usd") or 0.0) > 0 and row.get("route_leg_amounts_in"):
            amounts = _parse_amounts(row.get("route_leg_amounts_in"))
            if amounts and amounts[0] > 0:
                token_prices.setdefault(route_tokens[0], float(row.get("trade_size_usd") or 0.0) / amounts[0])

    ready: list[dict[str, Any]] = []
    for row in rows:
        if not bool(row.get("profitable")):
            continue
        invariant_status = str(row.get("leg_price_invariant_status") or "").strip()
        invariant_reason = str(row.get("leg_price_invariant_reason") or "").strip()
        if not invariant_status:
            ready.append(
                {
                    **_safe_route(row),
                    "payload_buildable": False,
                    "payload_validated": False,
                    "payload_reason": "LEG1/LEG2 executable price invariant missing",
                    "payload_rejection": "LEG_PRICE_INVARIANT_MISSING",
                }
            )
            continue
        if invariant_status != "LEG_PRICE_EDGE_VALID":
            ready.append(
                {
                    **_safe_route(row),
                    "payload_buildable": False,
                    "payload_validated": False,
                    "payload_reason": "LEG1/LEG2 executable price invariant failed",
                    "payload_rejection": invariant_reason or invariant_status,
                }
            )
            continue
        cycle = _route_to_cycle(row)
        if cycle is None:
            continue
        build = build_expanded_strategy_output_from_cycle(
            cycle,
            token_prices,
            executor_address=cfg.c1_executor_address,
            min_net_profit_usd=cfg.min_net_profit_usd,
            rpc_url=cfg.polygon_rpc,
        )
        route_status = {
            **_safe_route(row),
            "payload_buildable": build.strikeable,
            "payload_reason": build.reason,
            "payload_diagnostics": build.diagnostics,
        }
        if build.strikeable and build.strategy_output:
            try:
                engine.validate_opportunity(build.strategy_output.get("opportunity", {}))
                plan = engine.build_c1_plan(build.strategy_output)
                route_status["c1_payload"] = engine.simulate_only(plan)
                if _env_bool("REQUIRE_FORK_SIM", False) or _env_bool("REQUIRE_C1_ETH_CALL_PROOF", False):
                    proof = engine.eth_call_plan(plan, rpc_url=os.getenv("FORK_RPC_URL") or cfg.polygon_rpc)
                    route_status["c1_eth_call"] = proof
                    if not proof.get("ok"):
                        route_status["payload_validated"] = False
                        route_status["payload_rejection"] = f"c1 eth_call failed: {proof.get('error')}"
                        ready.append(route_status)
                        continue
                route_status["payload_validated"] = True
                route_status["tx_to"] = cfg.c1_executor_address
                route_status["receiver"] = build.strategy_output.get("flash_loan_receiver")
            except Exception as exc:  # noqa: BLE001
                route_status["payload_validated"] = False
                route_status["payload_rejection"] = str(exc)
        ready.append(route_status)
    return ready


async def _run_once(cycle_no: int) -> dict[str, Any]:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    os.environ["DRY_RUN"] = "true"

    from apex_omega_core.core.runtime_config import load_runtime_config
    from dry_run import run_live_opportunity_scan

    cfg = load_runtime_config()
    target_count = _env_int("AUTONOMOUS_SCAN_TARGET_COUNT", 20)
    target_label: int | str = "unbounded" if target_count <= 0 else target_count
    max_scans = None
    proof_attempts = max(1, _env_int("AUTONOMOUS_PROOF_ATTEMPTS", 1))
    executable_target_count = max(1, _env_int("AUTONOMOUS_EXECUTABLE_TARGET_COUNT", 1))
    max_flashloan_cap_usd = _env_float(
        "AUTONOMOUS_MAX_FLASHLOAN_CAP_USD",
        _env_float("MAX_FLASH_LOAN_USD", 100_000.0),
    )
    max_price_dev = _env_float("MAX_PRICE_DEV", 0.05)
    min_pool_tvl_usd = _env_float("MIN_POOL_TVL_USD", cfg.min_pool_tvl_usd)
    provider = os.getenv("FLASH_LOAN_PROVIDER", "balancer")
    enable_triangular = _env_bool("AUTONOMOUS_ENABLE_TRIANGULAR", True)
    enable_expanded_scan = _env_bool("AUTONOMOUS_ENABLE_EXPANDED_SCAN", True)

    validation_cfg = replace(cfg, min_pool_tvl_usd=min_pool_tvl_usd)
    started = time.time()
    rows: list[dict[str, Any]] = []
    payload_routes: list[dict[str, Any]] = []
    payload_validated: list[dict[str, Any]] = []
    attempts_completed = 0
    for attempt_no in range(1, proof_attempts + 1):
        attempts_completed = attempt_no
        records = await run_live_opportunity_scan(
            rpc_url=cfg.polygon_rpc,
            target_count=target_count,
            trade_size_usd=max_flashloan_cap_usd,
            flash_loan_provider=provider,
            min_pool_tvl_usd=min_pool_tvl_usd,
            max_price_dev=max_price_dev,
            min_net_profit_usd=cfg.min_net_profit_usd,
            enable_triangular=enable_triangular,
            enable_expanded_scan=enable_expanded_scan,
            expanded_max_hops=4,
            max_scans=max_scans,
            output_csv=str(CSV_PATH),
        )
        attempt_rows = [asdict(record) for record in records]
        rows.extend(attempt_rows)
        profitable_so_far = [row for row in rows if bool(row.get("profitable"))]
        payload_routes = _payload_ready_routes(profitable_so_far, validation_cfg)
        payload_validated = [row for row in payload_routes if row.get("payload_validated")]
        if len(payload_validated) >= executable_target_count:
            break

    profitable = [row for row in rows if bool(row.get("profitable"))]
    best = max(profitable, key=lambda row: float(row.get("e_profit") or 0.0), default=None)
    top_routes = [
        _safe_route(row)
        for row in sorted(
            profitable,
            key=lambda item: float(item.get("e_profit") or 0.0),
            reverse=True,
        )[:10]
    ]
    elapsed = time.time() - started
    buildable = [row for row in payload_routes if row.get("payload_buildable")]
    rejected = [row for row in payload_routes if not row.get("payload_validated")]
    best_executable = max(
        payload_validated,
        key=lambda row: float(row.get("e_profit") or 0.0),
        default=None,
    )
    live_quote = _live_quote_status()
    proof_checkpoint = _proof_checkpoint(
        payload_validated_count=len(payload_validated),
        payload_candidate_count=len(payload_routes),
        payload_rejected_count=len(rejected),
        raw_profitable_count=len(profitable),
        live_quote=live_quote,
    )

    return {
        "status": "running",
        "mode": "dry_run_no_broadcast",
        "autonomous_mode": "continuous_until_stopped",
        "cycle_no": cycle_no,
        "timestamp": time.time(),
        "elapsed_seconds": round(elapsed, 3),
        "chain_id": cfg.chain_id,
        "rpc": cfg.polygon_rpc,
        "provider": provider,
        "min_net_profit_usd": cfg.min_net_profit_usd,
        "min_pool_tvl_usd": min_pool_tvl_usd,
        "max_price_dev": max_price_dev,
        "price_deviation_gate": "disabled",
        "market_data_policy": _market_data_policy(),
        "target_records_per_cycle": target_label,
        "max_scan_rounds_per_cycle": "unbounded" if max_scans is None else max_scans,
        "sizing_policy": "opportunity_driven_optimal_flashloan_sizing",
        "configured_max_flashloan_cap_usd": max_flashloan_cap_usd,
        "enable_triangular": enable_triangular,
        "enable_expanded_scan": enable_expanded_scan,
        "expanded_graph_scan_enabled": _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True),
        "live_quote_enabled": _env_bool("LIVE_QUOTE_ENABLED", True),
        "proof_attempts": proof_attempts,
        "attempts_completed": attempts_completed,
        "executable_target_count": executable_target_count,
        "records": len(rows),
        "raw_profitable_count": len(profitable),
        "profitable_count": len(payload_validated),
        "executable_count": len(payload_validated),
        "best": _safe_route(best) if best else None,
        "best_executable": _safe_route(best_executable) if best_executable else None,
        "top_routes": top_routes,
        "payload_candidate_count": len(payload_routes),
        "payload_ready_count": len(buildable),
        "payload_validated_count": len(payload_validated),
        "payload_rejected_count": len(rejected),
        "payload_routes": payload_routes[:10],
        "proof_checkpoint": proof_checkpoint,
        "price_coverage": _price_coverage_status(),
        "live_quote": live_quote,
        "opportunity_scope": "multi_route_per_cycle_continuous",
        "csv_path": str(CSV_PATH),
    }


async def main() -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Run autonomous Apex-Omega scanner in dry-run mode.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit.")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    interval = _env_float("AUTONOMOUS_SCAN_INTERVAL_SEC", 10.0)
    target_count = _env_int("AUTONOMOUS_SCAN_TARGET_COUNT", 20)
    target_label: int | str = "unbounded" if target_count <= 0 else target_count
    max_scans = None
    cycle_no = 0
    last_status: dict[str, Any] | None = None
    _write_json(
        STATUS_PATH,
        {
            "status": "starting",
            "mode": "dry_run_no_broadcast",
            "autonomous_mode": "continuous_until_stopped",
            "timestamp": time.time(),
            "pid": os.getpid(),
            "interval_seconds": interval,
            "target_records_per_cycle": target_label,
            "max_scan_rounds_per_cycle": "unbounded" if max_scans is None else max_scans,
            "market_data_policy": _market_data_policy(),
            "top_routes": [],
            "payload_ready_count": 0,
            "payload_validated_count": 0,
            "payload_routes": [],
            "expanded_graph_scan_enabled": _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True),
            "live_quote_enabled": _env_bool("LIVE_QUOTE_ENABLED", True),
            "price_coverage": _price_coverage_status(),
            "opportunity_scope": "multi_route_per_cycle_continuous",
        },
    )

    while not _STOP:
        cycle_no += 1
        try:
            _write_json(
                STATUS_PATH,
                {
                    "status": "scanning",
                    "mode": "dry_run_no_broadcast",
                    "autonomous_mode": "continuous_until_stopped",
                    "cycle_no": cycle_no,
                    "timestamp": time.time(),
                    "pid": os.getpid(),
                    "interval_seconds": interval,
                    "target_records_per_cycle": target_label,
                    "max_scan_rounds_per_cycle": "unbounded" if max_scans is None else max_scans,
                    "market_data_policy": _market_data_policy(),
                    "top_routes": [],
                    "payload_ready_count": 0,
                    "payload_validated_count": 0,
                    "payload_routes": [],
                    "expanded_graph_scan_enabled": _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True),
                    "live_quote_enabled": _env_bool("LIVE_QUOTE_ENABLED", True),
                    "price_coverage": _price_coverage_status(),
                    "opportunity_scope": "multi_route_per_cycle_continuous",
                },
            )
            status = await _run_once(cycle_no)
            status["pid"] = os.getpid()
            status["interval_seconds"] = interval
            _write_json(STATUS_PATH, status)
            _append_event({"event": "scan_cycle_complete", **status})
            last_status = status
            if args.once:
                break
        except asyncio.TimeoutError:
            status = {
                "status": "timeout",
                "mode": "dry_run_no_broadcast",
                "autonomous_mode": "continuous_until_stopped",
                "cycle_no": cycle_no,
                "timestamp": time.time(),
                "pid": os.getpid(),
                "error": "Scan cycle exceeded internal asyncio timeout",
                "interval_seconds": interval,
                "market_data_policy": _market_data_policy(),
                "target_records_per_cycle": target_label,
                "max_scan_rounds_per_cycle": "unbounded" if max_scans is None else max_scans,
                "payload_ready_count": 0,
                "payload_validated_count": 0,
                "payload_routes": [],
                "expanded_graph_scan_enabled": _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True),
                "live_quote_enabled": _env_bool("LIVE_QUOTE_ENABLED", True),
                "price_coverage": _price_coverage_status(),
                "opportunity_scope": "multi_route_per_cycle_continuous",
            }
            _write_json(STATUS_PATH, status)
            _append_event({"event": "scan_cycle_timeout", **status})
            if args.once:
                # run_in_executor work can keep CPython alive after asyncio
                # timeout on Windows. The status artifact is already written;
                # exit hard so bounded dry-runs and supervisors fail closed.
                os._exit(0)
        except Exception as exc:  # noqa: BLE001
            status = {
                "status": "error",
                "mode": "dry_run_no_broadcast",
                "autonomous_mode": "continuous_until_stopped",
                "cycle_no": cycle_no,
                "timestamp": time.time(),
                "pid": os.getpid(),
                "error": str(exc),
                "interval_seconds": interval,
                "market_data_policy": _market_data_policy(),
                "payload_ready_count": 0,
                "payload_validated_count": 0,
                "payload_routes": [],
                "expanded_graph_scan_enabled": _env_bool("EXPANDED_GRAPH_SCAN_ENABLED", True),
                "live_quote_enabled": _env_bool("LIVE_QUOTE_ENABLED", True),
                "price_coverage": _price_coverage_status(),
            }
            _write_json(STATUS_PATH, status)
            _append_event({"event": "scan_cycle_error", **status})
            if args.once:
                break
        if _STOP:
            break
        await asyncio.sleep(interval)

    stopped_status = {
        **(last_status or {}),
        "status": (
            "completed_once"
            if args.once and last_status and last_status.get("status") not in {"error", "timeout"}
            else (last_status.get("status") if args.once and last_status else "stopped")
        ),
        "mode": "dry_run_no_broadcast",
        "cycle_no": cycle_no,
        "timestamp": time.time(),
        "pid": os.getpid(),
        "price_coverage": _price_coverage_status(),
        "market_data_policy": _market_data_policy(),
        "opportunity_scope": "multi_route_per_cycle_continuous",
    }
    if not last_status:
        stopped_status.update(
            {
                "top_routes": [],
                "payload_candidate_count": 0,
                "payload_ready_count": 0,
                "payload_validated_count": 0,
                "payload_routes": [],
            }
        )
    _write_json(STATUS_PATH, stopped_status)
    _append_event({"event": "scanner_stopped", "cycle_no": cycle_no, "timestamp": time.time(), "pid": os.getpid()})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
