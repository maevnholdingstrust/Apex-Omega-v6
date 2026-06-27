#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

OUT_PATH = ROOT / "runtime" / "route_level_fork_proofs.json"


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"dry-run CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _route_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": row.get("pair"),
        "route_id": row.get("route_id"),
        "hop_count": int(_as_float(row.get("hop_count"))),
        "trade_size_usd": _as_float(row.get("trade_size_usd")),
        "expected_net_edge": _as_float(row.get("expected_net_edge")),
        "e_profit": _as_float(row.get("e_profit")),
        "route_tokens": row.get("route_tokens"),
        "route_pools": row.get("route_pools"),
        "route_dexes": row.get("route_dexes"),
    }


def _build_plan(row: dict[str, Any], cfg: Any, rpc_url: str) -> tuple[Any, Any]:
    from scripts.autonomous_live_scanner import _route_to_cycle
    from apex_omega_core.core.execution_engine import ExecutionEngine
    from apex_omega_core.core.expanded_strategy_steps import build_expanded_strategy_output_from_cycle

    cycle = _route_to_cycle(row)
    if cycle is None:
        raise RuntimeError("row cannot be converted into a CycleRecord")

    token_prices: dict[str, float] = {}
    route_tokens = [item for item in str(row.get("route_tokens") or "").split("->") if item]
    if route_tokens and _as_float(row.get("trade_size_usd")) > 0 and row.get("route_leg_amounts_in"):
        amounts = json.loads(str(row.get("route_leg_amounts_in")))
        if amounts and float(amounts[0]) > 0:
            token_prices[route_tokens[0]] = _as_float(row.get("trade_size_usd")) / float(amounts[0])

    build = build_expanded_strategy_output_from_cycle(
        cycle,
        token_prices,
        executor_address=cfg.c1_executor_address,
        min_net_profit_usd=cfg.min_net_profit_usd,
        rpc_url=rpc_url,
    )
    if not build.strikeable or not build.strategy_output:
        raise RuntimeError(f"strategy output not buildable: {build.reason} {build.diagnostics}")

    engine = ExecutionEngine(cfg)
    engine.validate_opportunity(build.strategy_output.get("opportunity", {}))
    return build, engine.build_c1_plan(build.strategy_output)


def _prove_row(row: dict[str, Any], cfg: Any, rpc_url: str) -> dict[str, Any]:
    from apex_omega_core.core.execution_engine import ExecutionEngine

    route = _route_summary(row)
    started = time.time()
    try:
        build, plan = _build_plan(row, cfg, rpc_url)
        proof = ExecutionEngine(cfg).eth_call_plan(plan, rpc_url=rpc_url)
        return {
            "route": route,
            "proof_status": "passed" if proof.get("ok") else "failed",
            "payload": {
                "target_kind": plan.target,
                "asset": plan.compiled.asset,
                "flash_loan_amount": plan.flash_loan_amount,
                "min_profit": plan.compiled.min_profit,
                "calldata_len": len(plan.calldata),
                "calldata_keccak": Web3.to_hex(Web3.keccak(plan.calldata)),
            },
            "strategy_diagnostics": build.diagnostics,
            "eth_call": proof,
            "elapsed_seconds": round(time.time() - started, 3),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "route": route,
            "proof_status": "build_failed",
            "error": str(exc),
            "elapsed_seconds": round(time.time() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="No-broadcast route-level fork/eth_call proofs for discovered routes.")
    parser.add_argument("--csv", default=str(ROOT / "dry_run_results.csv"))
    parser.add_argument("--rpc", default="")
    parser.add_argument("--output", default=str(OUT_PATH))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--include-unprofitable", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    os.environ["DRY_RUN"] = "true"
    os.environ["BROADCAST_ENABLED"] = "false"
    os.environ["APEX_SEND_TX"] = "0"

    from apex_omega_core.core.runtime_config import load_runtime_config

    cfg = load_runtime_config()
    rpc_url = args.rpc or os.getenv("FORK_RPC_URL") or os.getenv("POLYGON_RPC_URL") or os.getenv("POLYGON_RPC") or cfg.polygon_rpc
    rows = _load_rows(Path(args.csv))
    candidate_rows = [
        row for row in rows
        if args.include_unprofitable or _as_bool(row.get("profitable"))
    ]
    candidate_rows.sort(key=lambda row: _as_float(row.get("e_profit") or row.get("expected_net_edge")), reverse=True)
    if args.limit > 0:
        candidate_rows = candidate_rows[: args.limit]

    payload = {
        "generated_at": time.time(),
        "mode": "no_broadcast_route_level_eth_call",
        "rpc": rpc_url,
        "csv": str(Path(args.csv).resolve()),
        "route_count": len(candidate_rows),
        "proofs": [_prove_row(row, cfg, rpc_url) for row in candidate_rows],
        "broadcast": {
            "enabled": False,
            "reason": "route proof runner only uses eth_call and never signs or sends",
        },
    }
    payload["passed_count"] = sum(1 for item in payload["proofs"] if item.get("proof_status") == "passed")
    payload["failed_count"] = sum(1 for item in payload["proofs"] if item.get("proof_status") == "failed")
    payload["build_failed_count"] = sum(1 for item in payload["proofs"] if item.get("proof_status") == "build_failed")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "mode": payload["mode"],
        "rpc": payload["rpc"],
        "route_count": payload["route_count"],
        "passed_count": payload["passed_count"],
        "failed_count": payload["failed_count"],
        "build_failed_count": payload["build_failed_count"],
        "output": str(out),
    }, indent=2))
    return 0 if payload["passed_count"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
