from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys, re

ROOT = Path.cwd()
CORE = ROOT / "python" / "apex_omega_core" / "core"
ENV = ROOT / ".env"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p):
    if p.exists():
        b = p.with_suffix(p.suffix + f".bak_finish_protocols_{STAMP}")
        shutil.copy2(p, b)
        print(f"[BACKUP] {b}")

def write(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    backup(p)
    p.write_text(s, encoding="utf-8", newline="\n")
    print(f"[WRITE] {p}")

def compile_py(p):
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
    print(f"[OK] compiled {p}")

def set_env(src, k, v):
    rx = re.compile(rf"^{re.escape(k)}=.*$", re.MULTILINE)
    line = f"{k}={v}"
    return rx.sub(line, src) if rx.search(src) else src.rstrip() + "\n" + line + "\n"

write(CORE / "v3_tick_lane.py", r'''
from __future__ import annotations

from dataclasses import dataclass

Q96 = 2 ** 96

@dataclass(frozen=True)
class V3PoolState:
    pool: str
    token0: str
    token1: str
    fee_tier: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    tick_spacing: int | None = None
    initialized_ticks_loaded: bool = False

def price_from_sqrt_price_x96(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    return (sqrt_price_x96 / Q96) ** 2 * (10 ** (decimals0 - decimals1))

def validate_v3_state(state: V3PoolState) -> tuple[bool, str]:
    if state.sqrt_price_x96 <= 0:
        return False, "missing_or_zero_sqrt_price_x96"
    if state.liquidity <= 0:
        return False, "missing_or_zero_liquidity"
    if state.fee_tier not in {100, 500, 3000, 10000}:
        return False, "unsupported_fee_tier"
    if not state.initialized_ticks_loaded:
        return False, "initialized_ticks_not_loaded"
    return True, "ok"

def quote_v3_exact_input_guarded(*, state: V3PoolState, amount_in: int) -> int:
    ok, reason = validate_v3_state(state)
    if not ok:
        raise ValueError(f"V3 quote rejected: {reason}")
    # Full initialized-tick traversal must be implemented before execution.
    raise NotImplementedError("V3 initialized-tick traversal quote not implemented yet")
''')

write(CORE / "curve_stable_lane.py", r'''
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class CurvePoolState:
    pool: str
    tokens: list[str]
    balances: list[int]
    decimals: list[int]
    amp: int
    fee_bps: int

def validate_curve_state(state: CurvePoolState) -> tuple[bool, str]:
    if len(state.tokens) < 2:
        return False, "not_enough_tokens"
    if len(state.balances) != len(state.tokens):
        return False, "balance_token_length_mismatch"
    if state.amp <= 0:
        return False, "missing_amp"
    if state.fee_bps < 0:
        return False, "invalid_fee"
    return True, "ok"

def quote_curve_guarded(*, state: CurvePoolState, i: int, j: int, dx: int) -> int:
    ok, reason = validate_curve_state(state)
    if not ok:
        raise ValueError(f"Curve quote rejected: {reason}")
    # Full StableSwap invariant must be implemented before execution.
    raise NotImplementedError("Curve StableSwap invariant quote not implemented yet")
''')

write(CORE / "balancer_v3_lane.py", r'''
from __future__ import annotations

from dataclasses import dataclass
from math import pow

BALANCER_V3_POLYGON_VAULT = "0xbA1333333333a1BA1108E8412f11850A5C319bA9"
BALANCER_V3_FLASH_FEE_BPS = 0

@dataclass(frozen=True)
class BalancerV3PoolState:
    pool: str
    pool_type: str
    tokens: list[str]
    balances_raw: list[int]
    last_live_balances: list[int]
    weights: list[float] | None
    swap_fee_bps: int
    vault: str = BALANCER_V3_POLYGON_VAULT

def validate_balancer_state(state: BalancerV3PoolState) -> tuple[bool, str]:
    if state.vault.lower() != BALANCER_V3_POLYGON_VAULT.lower():
        return False, "wrong_vault"
    if len(state.tokens) < 2:
        return False, "not_enough_tokens"
    if len(state.last_live_balances) != len(state.tokens):
        return False, "live_balance_token_length_mismatch"
    if any(int(x) <= 0 for x in state.last_live_balances):
        return False, "zero_live_balance"
    if state.swap_fee_bps < 0:
        return False, "invalid_swap_fee"
    if state.pool_type.upper() == "WEIGHTED":
        if not state.weights or len(state.weights) != len(state.tokens):
            return False, "missing_weights"
    return True, "ok"

def weighted_amount_out(
    amount_in: float,
    balance_in: float,
    weight_in: float,
    balance_out: float,
    weight_out: float,
    fee_bps: int,
) -> float:
    amount_after_fee = amount_in * (1.0 - fee_bps / 10_000.0)
    ratio = balance_in / (balance_in + amount_after_fee)
    return balance_out * (1.0 - pow(ratio, weight_in / weight_out))

def quote_balancer_weighted_guarded(*, state: BalancerV3PoolState, token_in_index: int, token_out_index: int, amount_in: float) -> float:
    ok, reason = validate_balancer_state(state)
    if not ok:
        raise ValueError(f"Balancer V3 quote rejected: {reason}")
    if state.pool_type.upper() != "WEIGHTED":
        raise NotImplementedError("Only Balancer weighted guard is installed; stable/composable stable remains gated")
    return weighted_amount_out(
        amount_in=amount_in,
        balance_in=float(state.last_live_balances[token_in_index]),
        weight_in=float(state.weights[token_in_index]),
        balance_out=float(state.last_live_balances[token_out_index]),
        weight_out=float(state.weights[token_out_index]),
        fee_bps=state.swap_fee_bps,
    )

def balancer_flash_fee_bps() -> int:
    return BALANCER_V3_FLASH_FEE_BPS
''')

write(CORE / "protocol_execution_gates.py", r'''
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str
    metadata: dict[str, Any]

def gate_protocol_candidate(candidate: dict[str, Any]) -> GateResult:
    family = str(candidate.get("pool_family") or candidate.get("pool_type") or "unknown")
    tvl = float(candidate.get("tvl_usd") or 0.0)
    tvl_verified = bool(candidate.get("tvl_verified") or False)
    execution_supported = bool(candidate.get("execution_supported") or False)

    if tvl <= 0:
        return GateResult(False, "missing_tvl_usd", candidate)
    if not tvl_verified:
        return GateResult(False, "tvl_unverified", candidate)

    if family == "v2_cpmm":
        if not execution_supported:
            return GateResult(False, "v2_execution_not_supported", candidate)
        return GateResult(True, "v2_ready", candidate)

    if family in {"v3_clmm", "algebra_clmm"}:
        required = ("sqrt_price_x96", "tick", "liquidity", "fee_tier", "initialized_ticks_loaded")
        missing = [k for k in required if candidate.get(k) in (None, False)]
        if missing:
            return GateResult(False, "v3_missing_" + ",".join(missing), candidate)
        return GateResult(False, "v3_quote_calldata_fork_parity_required", candidate)

    if family == "curve_stable":
        required = ("balances", "amp", "fee_bps")
        missing = [k for k in required if candidate.get(k) is None]
        if missing:
            return GateResult(False, "curve_missing_" + ",".join(missing), candidate)
        return GateResult(False, "curve_invariant_calldata_fork_parity_required", candidate)

    if family in {"balancer_weighted", "balancer_stable"}:
        required = ("vault", "tokens", "last_live_balances", "swap_fee_bps")
        missing = [k for k in required if candidate.get(k) is None]
        if missing:
            return GateResult(False, "balancer_missing_" + ",".join(missing), candidate)
        return GateResult(False, "balancer_vault_calldata_fork_parity_required", candidate)

    return GateResult(False, "unknown_protocol_family", candidate)
''')

write(CORE / "dna_protocol_labels.py", r'''
from __future__ import annotations

def build_protocol_dna_label(candidate: dict) -> dict:
    return {
        "pool_address": candidate.get("pool_address") or candidate.get("address"),
        "dex_name": candidate.get("dex_name") or candidate.get("dex"),
        "pool_family": candidate.get("pool_family") or candidate.get("pool_type"),
        "math_mode": candidate.get("math_mode"),
        "fee_bps": candidate.get("fee_bps"),
        "fee_tier": candidate.get("fee_tier"),
        "quote_engine": candidate.get("quote_engine"),
        "calldata_engine": candidate.get("calldata_engine"),
        "execution_supported": bool(candidate.get("execution_supported")),
        "tvl_usd": float(candidate.get("tvl_usd") or 0.0),
        "tvl_verified": bool(candidate.get("tvl_verified")),
        "fork_required": True,
        "broadcast_allowed": False,
    }
''')

write(CORE / "finish_today_shadow_runner.py", r'''
from __future__ import annotations

import json
from pathlib import Path
from .protocol_execution_gates import gate_protocol_candidate
from .dna_protocol_labels import build_protocol_dna_label

def run_protocol_shadow_check(input_path: str = "runtime/discovery_universe.json", output_path: str = "runtime/protocol_shadow_report.json") -> dict:
    src = Path(input_path)
    if not src.exists():
        payload = {"ok": False, "reason": "missing_discovery_universe", "rows": []}
        Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    data = json.loads(src.read_text(encoding="utf-8"))
    rows = []

    for pool in data.get("pools", []):
        gate = gate_protocol_candidate(pool)
        rows.append({
            "pool": pool.get("pool_address"),
            "gate_ok": gate.ok,
            "gate_reason": gate.reason,
            "dna": build_protocol_dna_label(pool),
        })

    payload = {
        "ok": True,
        "pool_count": len(rows),
        "executable_count": sum(1 for r in rows if r["gate_ok"]),
        "blocked_count": sum(1 for r in rows if not r["gate_ok"]),
        "rows": rows,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

if __name__ == "__main__":
    result = run_protocol_shadow_check()
    print(json.dumps({k:v for k,v in result.items() if k != "rows"}, indent=2))
''')

env = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
for k, v in {
    "EXECUTION_ENABLED": "false",
    "BROADCAST_ENABLED": "false",
    "ALLOW_V3_EXECUTION": "false",
    "ALLOW_CURVE_EXECUTION": "false",
    "ALLOW_BALANCER_EXECUTION": "false",
    "REQUIRE_FORK_SIM": "true",
    "REQUIRE_PROTOCOL_DNA_LABELS": "true",
    "BALANCER_V3_VAULT": "0xbA1333333333a1BA1108E8412f11850A5C319bA9",
    "BALANCER_V3_FLASH_FEE_BPS": "0",
}.items():
    env = set_env(env, k, v)

backup(ENV)
ENV.write_text(env, encoding="utf-8", newline="\n")

for p in [
    CORE / "v3_tick_lane.py",
    CORE / "curve_stable_lane.py",
    CORE / "balancer_v3_lane.py",
    CORE / "protocol_execution_gates.py",
    CORE / "dna_protocol_labels.py",
    CORE / "finish_today_shadow_runner.py",
]:
    compile_py(p)

print("[DONE] Finish-today protocol lanes installed.")
print("Run shadow check:")
print("  python .\\python\\apex_omega_core\\core\\finish_today_shadow_runner.py")
