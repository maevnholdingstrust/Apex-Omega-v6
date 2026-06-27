#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

OUT_PATH = ROOT / "runtime" / "fork_route_proof.json"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"dry-run CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _select_best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profitable = [row for row in rows if _as_bool(row.get("profitable"))]
    if not profitable:
        raise RuntimeError("no profitable rows in dry_run_results.csv")
    return max(profitable, key=lambda row: _as_float(row.get("e_profit") or row.get("expected_net_edge")))


def _owner_address(cfg: Any) -> str:
    if cfg.executor_private_key:
        return Account.from_key(cfg.executor_private_key).address
    owner = os.getenv("EXECUTOR_WALLET_ADDRESS") or os.getenv("OWNER_ADDRESS") or os.getenv("OPERATOR_ADDRESS")
    if owner:
        return Web3.to_checksum_address(owner)
    raise RuntimeError("no EXECUTOR_PRIVATE_KEY or owner wallet address available for eth_call sender")


def _error_text(exc: Exception) -> str:
    text = str(exc)
    if len(text) > 2000:
        return text[:2000] + "...<truncated>"
    return text


def _build_plan(row: dict[str, Any], cfg: Any) -> tuple[Any, Any]:
    from scripts.autonomous_live_scanner import _route_to_cycle
    from apex_omega_core.core.execution_engine import ExecutionEngine
    from apex_omega_core.core.expanded_strategy_steps import build_expanded_strategy_output_from_cycle

    cycle = _route_to_cycle(row)
    if cycle is None:
        raise RuntimeError("selected row cannot be converted back into a CycleRecord")

    token_prices: dict[str, float] = {}
    route_tokens = [item for item in str(row.get("route_tokens") or "").split("->") if item]
    if route_tokens and _as_float(row.get("trade_size_usd")) > 0 and row.get("route_leg_amounts_in"):
        import json as _json

        amounts = _json.loads(str(row.get("route_leg_amounts_in")))
        if amounts and float(amounts[0]) > 0:
            token_prices[route_tokens[0]] = _as_float(row.get("trade_size_usd")) / float(amounts[0])

    build = build_expanded_strategy_output_from_cycle(
        cycle,
        token_prices,
        executor_address=cfg.c1_executor_address,
        min_net_profit_usd=cfg.min_net_profit_usd,
        rpc_url=cfg.polygon_rpc,
    )
    if not build.strikeable or not build.strategy_output:
        raise RuntimeError(f"strategy output not buildable: {build.reason} {build.diagnostics}")

    engine = ExecutionEngine(cfg)
    engine.validate_opportunity(build.strategy_output.get("opportunity", {}))
    return build, engine.build_c1_plan(build.strategy_output)


def main() -> int:
    parser = argparse.ArgumentParser(description="No-broadcast fork/eth_call proof for the best discovered route.")
    parser.add_argument("--csv", default=str(ROOT / "dry_run_results.csv"), help="dry-run CSV path")
    parser.add_argument("--rpc", default="", help="simulation RPC; defaults to FORK_RPC_URL, then POLYGON_RPC_URL/POLYGON_RPC")
    parser.add_argument("--output", default=str(OUT_PATH), help="proof JSON output path")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    os.environ["DRY_RUN"] = "true"
    os.environ["BROADCAST_ENABLED"] = "false"
    os.environ["APEX_SEND_TX"] = "0"

    from apex_omega_core.core.runtime_config import load_runtime_config

    cfg = load_runtime_config()
    rows = _load_rows(Path(args.csv))
    best = _select_best(rows)
    build, plan = _build_plan(best, cfg)

    rpc_url = (
        args.rpc
        or os.getenv("FORK_RPC_URL")
        or os.getenv("POLYGON_RPC_URL")
        or os.getenv("POLYGON_RPC")
        or cfg.polygon_rpc
    )
    if not rpc_url:
        raise RuntimeError("no simulation RPC configured")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    owner = _owner_address(cfg)
    target = Web3.to_checksum_address(cfg.c1_executor_address)
    calldata_hex = Web3.to_hex(plan.calldata)
    code = w3.eth.get_code(target)
    call_ok = False
    call_output = None
    call_error = None
    gas_estimate = None
    block_number = None
    chain_id = None

    try:
        chain_id = int(w3.eth.chain_id)
        block_number = int(w3.eth.block_number)
        if not code:
            raise RuntimeError(f"target has no bytecode at {target} on selected RPC")
        tx = {"from": Web3.to_checksum_address(owner), "to": target, "data": calldata_hex, "value": 0}
        try:
            gas_estimate = int(w3.eth.estimate_gas(tx))
        except Exception as exc:  # noqa: BLE001
            gas_estimate = None
            call_error = f"gas_estimate_failed: {_error_text(exc)}"
        output = w3.eth.call(tx, block_identifier="latest")
        call_ok = True
        call_output = Web3.to_hex(output)
    except Exception as exc:  # noqa: BLE001
        call_error = call_error or _error_text(exc)

    proof = {
        "timestamp": time.time(),
        "mode": "no_broadcast_eth_call",
        "rpc": rpc_url,
        "chain_id": chain_id,
        "block_number": block_number,
        "selected_route": {
            "pair": best.get("pair"),
            "route_id": best.get("route_id"),
            "hop_count": int(float(best.get("hop_count") or 0)),
            "trade_size_usd": _as_float(best.get("trade_size_usd")),
            "expected_net_edge": _as_float(best.get("expected_net_edge")),
            "e_profit": _as_float(best.get("e_profit")),
            "route_tokens": best.get("route_tokens"),
            "route_pools": best.get("route_pools"),
            "route_dexes": best.get("route_dexes"),
        },
        "payload": {
            "target_kind": plan.target,
            "tx_to": target,
            "from": Web3.to_checksum_address(owner),
            "asset": plan.compiled.asset,
            "flash_loan_amount": plan.flash_loan_amount,
            "min_profit": plan.compiled.min_profit,
            "calldata_len": len(plan.calldata),
            "calldata_keccak": Web3.to_hex(Web3.keccak(plan.calldata)),
            "calldata": calldata_hex,
        },
        "strategy_diagnostics": build.diagnostics,
        "target_has_code": bool(code),
        "target_code_bytes": len(code),
        "eth_call": {
            "ok": call_ok,
            "output": call_output,
            "error": call_error,
            "gas_estimate": gas_estimate,
        },
        "broadcast": {
            "enabled": False,
            "reason": "proof script uses eth_call only and never signs or sends",
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({k: proof[k] for k in ("mode", "rpc", "chain_id", "block_number", "selected_route", "target_has_code", "target_code_bytes", "eth_call", "broadcast")}, indent=2))
    return 0 if call_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
