from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import Any

from web3 import Web3

from .contract_targets import C1_TARGET, C2_TARGET
from .execution_compiler import ExecutionCompiler
from .execution_engine import ExecutionEngine
from .live_strategy_steps import build_live_strategy_output_from_state
from .runtime_config import RuntimeConfig, load_runtime_config
from .scanner_strategy_pipeline import run_scanner_strategy_pipeline
from .slippage_sentinel import SlippageSentinel

# Heuristic pressure normalizers used by _estimate_p_fill.
# They map mempool load into a bounded fill probability penalty:
# - pending txs are scaled by _PENDING_PRESSURE_DIVISOR
# - swap-like txs (higher MEV contention) are scaled more aggressively
# These values are intentionally conservative and keep p_fill in [5%, 99%].
_PENDING_PRESSURE_DIVISOR = 20_000.0
_SWAP_PRESSURE_DIVISOR = 200.0
_MIN_P_FILL = 0.05
_MAX_P_FILL = 0.99
logger = logging.getLogger(__name__)


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


def _compile_payloads(
    strategy_output: dict[str, Any],
    *,
    c1_target: str = C1_TARGET,
    c2_target: str = C2_TARGET,
) -> dict[str, Any]:
    compiler = ExecutionCompiler()
    c1 = compiler.compile_for_institutional(strategy_output)
    c2 = compiler.compile_for_ultimate(strategy_output)
    c1_hash = Web3.keccak(c1.encoded_payload).hex()
    c2_hash = Web3.keccak(c2.encoded_payload).hex()
    return {
        "c1": {
            "target": c1_target,
            "contract": "InstitutionalExecutor",
            "payload_bytes": len(c1.encoded_payload),
            "payload_keccak": c1_hash,
            "asset": c1.asset,
            "min_profit": c1.min_profit,
            "broadcast": False,
            "broadcast_reason": "dry-run only",
        },
        "c2": {
            "target": c2_target,
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
        payloads = _compile_payloads(
            build.strategy_output,
            c1_target=cfg.c1_executor_address or C1_TARGET,
            c2_target=cfg.c2_executor_address or C2_TARGET,
        )
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


def _discover_pending_swaps(config: RuntimeConfig, sample_limit: int = 32) -> dict[str, Any]:
    try:
        w3 = Web3(Web3.HTTPProvider(config.primary_rpc))
        pending = w3.eth.get_block("pending", full_transactions=True)
        txs = list((pending or {}).get("transactions", []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pending tx discovery failed: %s", exc)
        return {
            "status": "error",
            "pending_total": 0,
            "sampled": 0,
            "swap_like": 0,
            "error": "pending tx discovery unavailable",
        }

    swap_like = 0
    sampled = min(sample_limit, len(txs))
    for tx in txs[:sampled]:
        tx_input = str(tx.get("input") or "")
        if len(tx_input) >= 10 and tx_input.startswith("0x"):
            selector = tx_input[:10].lower()
            if selector in {
                "0x38ed1739",
                "0x8803dbee",
                "0x7ff36ab5",
                "0x4a25d94a",
                "0x18cbafe5",
                "0x414bf389",
                "0xc04b8d59",
                "0xdb3e2198",
            }:
                swap_like += 1
    return {
        "status": "ok",
        "pending_total": len(txs),
        "sampled": sampled,
        "swap_like": swap_like,
        "sample_limit": sample_limit,
    }


def _estimate_p_fill(pending_discovery: dict[str, Any]) -> float:
    pending_total = max(0.0, _safe_float(pending_discovery.get("pending_total"), 0.0))
    swap_like = max(0.0, _safe_float(pending_discovery.get("swap_like"), 0.0))
    pressure = pending_total / _PENDING_PRESSURE_DIVISOR + swap_like / _SWAP_PRESSURE_DIVISOR
    return max(_MIN_P_FILL, min(_MAX_P_FILL, 1.0 - pressure))


def build_live_execution_payloads(
    *,
    limit: int = 5,
    max_pairs: int = 24,
    min_spread_bps: float | None = None,
    auto_submit: bool = False,
    config: RuntimeConfig | None = None,
    engine: ExecutionEngine | None = None,
) -> dict[str, Any]:
    cfg = config or load_runtime_config()
    blockers = live_execution_blockers(cfg)
    min_spread = cfg.min_raw_spread_bps if min_spread_bps is None else float(min_spread_bps)
    pending_discovery = _discover_pending_swaps(cfg)
    p_fill = _estimate_p_fill(pending_discovery)
    pipeline = run_scanner_strategy_pipeline(
        executor_address=cfg.c1_executor_address or C1_TARGET,
        max_pairs=max_pairs,
        min_spread_bps=min_spread,
        max_candidates=limit,
        min_net_profit_usd=cfg.min_net_profit_usd,
        gas_cost_usd=cfg.c1_gas_usd,
        flash_fee_bps=cfg.flash_loan_fee_bps,
        risk_buffer_usd=cfg.risk_buffer_usd,
    )
    active_engine = engine or ExecutionEngine(cfg)
    cards: list[dict[str, Any]] = []
    submit_enabled = bool(auto_submit and not blockers)

    for idx, candidate in enumerate(pipeline.candidates, start=1):
        op = candidate.opportunity
        build = candidate.build
        if not build or not build.strikeable or not build.strategy_output:
            cards.append(
                {
                    "card_id": f"LIVE-{idx:02d}",
                    "mode": "LIVE_DISCOVERY",
                    "discovery": {
                        "pair": f"{op.base_symbol}/{op.quote_symbol}",
                        "buy_venue": op.buy_venue,
                        "sell_venue": op.sell_venue,
                        "raw_spread_bps": op.raw_spread_bps,
                    },
                    "execution_ready": False,
                    "reason": candidate.reason,
                }
            )
            continue

        strategy_output = build.strategy_output
        opportunity = strategy_output.get("opportunity", {})
        owner_submission_edge = opportunity.get("owner_submission_edge_usd")
        # Prefer owner_submission_edge_usd when present; fall back to net_profit_usd
        # for older payloads that predate owner-paid gas accounting.
        p_net_usd = _safe_float(owner_submission_edge, _safe_float(opportunity.get("net_profit_usd")))
        guard_pass = (p_net_usd * p_fill) > 0.0
        payloads = _compile_payloads(
            strategy_output,
            c1_target=cfg.c1_executor_address or C1_TARGET,
            c2_target=cfg.c2_executor_address or C2_TARGET,
        )

        card: dict[str, Any] = {
            "card_id": f"LIVE-{idx:02d}",
            "mode": "LIVE_DISCOVERY",
            "discovery": {
                "pair": f"{op.base_symbol}/{op.quote_symbol}",
                "buy_venue": op.buy_venue,
                "sell_venue": op.sell_venue,
                "buy_pool": op.buy_pool,
                "sell_pool": op.sell_pool,
                "raw_spread_bps": op.raw_spread_bps,
            },
            "execution_ready": True,
            "reason": build.reason,
            "guardrail": {
                "p_net_usd": p_net_usd,
                "p_fill": p_fill,
                "pnet_x_pfill": p_net_usd * p_fill,
                "pass": guard_pass,
            },
            "payloads": payloads,
            "steps": _json_safe(strategy_output.get("steps", [])),
            "broadcast": {
                "requested": bool(auto_submit),
                "enabled": False,
                "submitted": False,
                "reason": "auto submission not requested",
            },
        }
        if not auto_submit:
            cards.append(card)
            continue
        if blockers:
            card["broadcast"] = {
                "requested": True,
                "enabled": False,
                "submitted": False,
                "reason": "live blockers present",
                "blockers": blockers,
            }
            cards.append(card)
            continue
        if not guard_pass:
            card["broadcast"] = {
                "requested": True,
                "enabled": submit_enabled,
                "submitted": False,
                "reason": "guardrail rejected (P_net × P(fill) <= 0)",
            }
            cards.append(card)
            continue

        try:
            active_engine.validate_opportunity(opportunity)
            c1_plan = active_engine.build_c1_plan(strategy_output)
            raw_tx = active_engine.sign_transaction(c1_plan)
            submission = active_engine.execute_bundle(raw_tx)
            card["broadcast"] = {
                "requested": True,
                "enabled": submit_enabled,
                "submitted": True,
                "target": "c1",
                "relay_results": [
                    {
                        "relay": getattr(result, "relay", ""),
                        "status": getattr(result, "status", ""),
                        "latency_ms": _safe_float(getattr(result, "latency_ms", 0.0)),
                        "error": getattr(result, "error", None),
                    }
                    for result in submission
                ],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("bundle submission failed for %s: %s", card["card_id"], exc)
            card["broadcast"] = {
                "requested": True,
                "enabled": submit_enabled,
                "submitted": False,
                "reason": "submission failed",
                "error": type(exc).__name__,
            }
        cards.append(card)

    return {
        "mode": "LIVE_DISCOVERY_REALTIME_ENCODING",
        "count": len(cards),
        "requested": limit,
        "auto_submit_requested": bool(auto_submit),
        "auto_submit_enabled": submit_enabled,
        "live_blockers": blockers,
        "tx_discovery": pending_discovery,
        "p_fill_estimate": p_fill,
        "cards": cards,
    }
