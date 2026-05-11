from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from web3 import Web3

from .contract_targets import C1_TARGET, C2_TARGET
from .execution_compiler import ExecutionCompiler
from .live_strategy_steps import build_live_strategy_output_from_state
from .runtime_config import RuntimeConfig, load_runtime_config
from .slippage_sentinel import SlippageSentinel


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def live_execution_blockers(config: RuntimeConfig | None = None) -> list[str]:
    cfg = config or load_runtime_config()
    blockers: list[str] = []
    if not cfg.live_trading_enabled:
        blockers.append("LIVE_TRADING_ENABLED is false")
    if cfg.dry_run:
        blockers.append("DRY_RUN is true")
    blockers.extend(cfg.missing_for_live())
    if not cfg.c1_executor_address:
        blockers.append("C1 target is missing")
    if not cfg.c2_executor_address:
        blockers.append("C2 target is missing")
    if not cfg.relays:
        blockers.append("No private relay endpoints configured; public mempool fallback must be explicitly approved")
    return sorted(set(blockers))


def _fallback_states(limit: int) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for i in range(limit):
        # Deterministic profitable CPMM states; each card remains executable via
        # the canonical USDCe -> WMATIC -> USDCe route builder.
        bump = i * 0.0125
        states.append(
            {
                "source": "deterministic_local_dry_run",
                "pair": "USDCe/WMATIC",
                "buy_dex": "quickswap_v2",
                "sell_dex": "uniswap_v3",
                "buy_pool": f"dryrun-c1-buy-{i + 1:02d}",
                "sell_pool": f"dryrun-c1-sell-{i + 1:02d}",
                "fee1": 0.003,
                "r1_in": 1_000_000.0 + (i * 35_000.0),
                "r1_out": (2_520_000.0 + (i * 78_000.0)) * (1.0 + bump),
                "fee2": 0.003,
                "r2_in": 2_590_000.0 + (i * 80_000.0),
                "r2_out": 1_140_000.0 + (i * 46_000.0),
            }
        )
    return states


def _states_from_csv(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    states: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if _safe_float(row.get("expected_net_edge")) <= 0:
                continue
            states.append(
                {
                    "source": "dry_run_results_csv",
                    "pair": row.get("pair") or "USDCe/WMATIC",
                    "buy_dex": row.get("buy_dex") or "quickswap_v2",
                    "sell_dex": row.get("sell_dex") or "uniswap_v3",
                    "buy_pool": row.get("buy_pool") or "",
                    "sell_pool": row.get("sell_pool") or "",
                    "fee1": 0.003,
                    "r1_in": max(1.0, _safe_float(row.get("trade_size_usd"), 10_000.0) * 100.0),
                    "r1_out": max(1.0, _safe_float(row.get("trade_size_usd"), 10_000.0) * 255.0),
                    "fee2": 0.003,
                    "r2_in": max(1.0, _safe_float(row.get("trade_size_usd"), 10_000.0) * 260.0),
                    "r2_out": max(1.0, _safe_float(row.get("trade_size_usd"), 10_000.0) * 102.0),
                    "csv_math": dict(row),
                }
            )
            if len(states) >= limit:
                break
    return states


def _bps(value: float, basis: float) -> float:
    return 0.0 if basis <= 0 else (value / basis) * 10_000.0


def _compile_payloads(strategy_output: dict[str, Any]) -> dict[str, Any]:
    compiler = ExecutionCompiler()
    c1 = compiler.compile_for_institutional(strategy_output)
    c2 = compiler.compile_for_ultimate(strategy_output)
    c1_hash = Web3.keccak(c1.encoded_payload).hex()
    c2_hash = Web3.keccak(c2.encoded_payload).hex()
    return {
        "c1": {
            "target": C1_TARGET,
            "contract": "InstitutionalExecutor",
            "payload_bytes": len(c1.encoded_payload),
            "payload_keccak": c1_hash,
            "asset": c1.asset,
            "min_profit": c1.min_profit,
            "broadcast": False,
            "broadcast_reason": "dry-run only",
        },
        "c2": {
            "target": C2_TARGET,
            "contract": "UltimateArbitrageExecutor",
            "payload_bytes": len(c2.encoded_payload),
            "payload_keccak": c2_hash,
            "merkle_leaf": c2_hash,
            "merkle_proof_required_for_live": True,
            "asset": c2.asset,
            "min_profit": c2.min_profit,
            "broadcast": False,
            "broadcast_reason": "dry-run only",
        },
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def build_execution_dna_cards(
    *,
    limit: int = 20,
    csv_path: str | Path | None = None,
    config: RuntimeConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_runtime_config()
    path = Path(csv_path) if csv_path is not None else Path.cwd() / "dry_run_results.csv"
    states = _states_from_csv(path, limit)
    if len(states) < limit:
        states.extend(_fallback_states(limit - len(states)))

    sentinel = SlippageSentinel()
    cards: list[dict[str, Any]] = []
    for idx, state in enumerate(states[:limit], start=1):
        build = build_live_strategy_output_from_state(
            state,
            executor_address=cfg.c1_executor_address or C1_TARGET,
            min_net_profit_usd=0.0,
            gas_cost_usd=cfg.c1_gas_usd,
            flash_fee_bps=cfg.flash_loan_fee_bps,
            risk_buffer_usd=cfg.risk_buffer_usd,
        )
        if not build.strikeable or not build.strategy_output:
            continue
        diagnostics = build.diagnostics or {}
        amount_in = _safe_float(diagnostics.get("amount_in"))
        math = sentinel.two_leg_arb_profit(
            amount_in,
            _safe_float(state["fee1"]),
            _safe_float(state["r1_in"]),
            _safe_float(state["r1_out"]),
            _safe_float(state["fee2"]),
            _safe_float(state["r2_in"]),
            _safe_float(state["r2_out"]),
            c_gas=0.0,
            flash_loan_fee_rate=cfg.flash_loan_fee_bps / 10_000.0,
            c_other=cfg.risk_buffer_usd,
        )
        token_net = _safe_float(math["p_net"])
        c1_owner_edge = token_net - cfg.c1_gas_usd
        c1_strike = c1_owner_edge > 0
        c2_owner_edge = token_net - cfg.c2_gas_usd
        c2_action = "POTENTIAL_STRIKE_AFTER_C1" if c1_strike and c2_owner_edge > 0 else "NO_OP"
        payloads = _compile_payloads(build.strategy_output)
        gross = _safe_float(math["p_gross"])
        flash_fee = amount_in * (cfg.flash_loan_fee_bps / 10_000.0)
        cards.append(
            {
                "card_id": f"DNA-{idx:02d}",
                "cycle_id": f"dryrun-cycle-{idx:02d}",
                "mode": "NO_BROADCAST_DRY_RUN",
                "generated_at": time.time(),
                "source": state.get("source"),
                "pair": state.get("pair"),
                "venues": {
                    "buy_dex": state.get("buy_dex"),
                    "sell_dex": state.get("sell_dex"),
                    "buy_pool": state.get("buy_pool"),
                    "sell_pool": state.get("sell_pool"),
                },
                "state": {
                    "fee1": state["fee1"],
                    "r1_in": state["r1_in"],
                    "r1_out": state["r1_out"],
                    "fee2": state["fee2"],
                    "r2_in": state["r2_in"],
                    "r2_out": state["r2_out"],
                },
                "math": {
                    "amount_in": amount_in,
                    "b_out_1": math["b_out_1"],
                    "a_out_2": math["a_out_2"],
                    "p_gross": gross,
                    "flash_fee_rate": cfg.flash_loan_fee_bps / 10_000.0,
                    "flash_fee": flash_fee,
                    "risk_buffer": cfg.risk_buffer_usd,
                    "p_net_route_token": token_net,
                    "c1_gas_usd_owner_paid": cfg.c1_gas_usd,
                    "c2_gas_usd_owner_paid": cfg.c2_gas_usd,
                    "c1_owner_submission_edge": c1_owner_edge,
                    "c2_owner_submission_edge": c2_owner_edge,
                    "gross_bps": _bps(gross, amount_in),
                    "flash_fee_bps": cfg.flash_loan_fee_bps,
                    "c1_gas_bps_for_ranking": _bps(cfg.c1_gas_usd, amount_in),
                    "route_net_bps": _bps(token_net, amount_in),
                    "c1_owner_edge_bps": _bps(c1_owner_edge, amount_in),
                },
                "cycle": {
                    "c1": {
                        "decision": "STRIKE" if c1_strike else "NO_OP",
                        "target": payloads["c1"]["target"],
                        "reason": "positive owner submission edge" if c1_strike else "non-positive owner submission edge",
                    },
                    "c2": {
                        "decision": c2_action,
                        "target": payloads["c2"]["target"],
                        "reason": "only eligible after C1 executes and post-state still has residual EV",
                    },
                },
                "payloads": payloads,
                "steps": _json_safe(build.strategy_output.get("steps", [])),
                "broadcast": {
                    "enabled": False,
                    "reason": "dry-run endpoint never signs or submits transactions",
                },
            }
        )
    return cards
