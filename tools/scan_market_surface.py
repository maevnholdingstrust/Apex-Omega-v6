# tools/scan_market_surface.py
"""
Discover pools → build size ladder → classify zones →
apply hard execution gates → print C1-eligible candidates.
Run from repo root:
    $env:PYTHONPATH = "$PWD\\python"
    python tools\\scan_market_surface.py | Select-Object -First 10
"""

from __future__ import annotations

import os
from decimal import Decimal

# --- internal imports -------------------------------------------------
from apex_omega_core.scanner.dex_intake import discover_pools
from apex_omega_core.ladder.size_ladder import build_size_ladder
from apex_omega_core.ladder.zone import (
    classify_flash_ladder_zone,
    is_size_zone_allowed_for_c1,
)
from apex_omega_core.safety.execution_gates import gate_candidate
# helper just for nicer print; remove if not present
from apex_omega_core.tools.cli_helpers import pretty_usd  
# ----------------------------------------------------------------------

_MIN_NET_PROFIT_USD = Decimal(os.getenv("C1_MIN_PROFIT_USD", "5"))


def _passes_hard_gate(raw_op: dict) -> bool:
    """Return True only if candidate passes execution-gate layer."""
    return gate_candidate(raw_op).reason is None


def _build_ladder_with_zones(raw_op: dict) -> list[dict]:
    ladder = build_size_ladder(raw_op)
    for p in ladder:
        fraction = p["amount_in_usd"] / raw_op["leg1_tvl_usd"]
        p["fraction_of_leg1_tvl"] = fraction
        p["zone"] = classify_flash_ladder_zone(fraction)
    return ladder


def _select_best_c1_size(ladder: list[dict]) -> dict | None:
    eligible = [
        p for p in ladder
        if is_size_zone_allowed_for_c1(p["zone"])
        and p["net_profit_usd"] > _MIN_NET_PROFIT_USD
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda x: x["net_profit_usd"])


def main() -> None:
    print("→ discovering pools …")
    raw_ops = discover_pools()
    print(f"→ {len(raw_ops)} raw opportunities")

    for op in filter(_passes_hard_gate, raw_ops):
        ladder = _build_ladder_with_zones(op)
        best = _select_best_c1_size(ladder)
        if not best:
            continue

        pair = op["pair"]
        size = pretty_usd(best["amount_in_usd"])
        profit = pretty_usd(best["net_profit_usd"])
        zone = best["zone"]
        print(f"{pair:<18}  size={size:<10}  net={profit:<10}  zone={zone}")


if __name__ == "__main__":
    main()