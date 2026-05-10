
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
