
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
