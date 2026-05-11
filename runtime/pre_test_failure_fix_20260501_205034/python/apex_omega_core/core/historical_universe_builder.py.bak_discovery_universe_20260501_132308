
from __future__ import annotations

import json
from pathlib import Path
from .discovery_universe import DiscoveryUniverse


DNA_LOG_PATHS = [
    Path("logs/dry_run_cycle_pairs.jsonl"),
    Path("logs/dry_run_dna_cards.jsonl"),
    Path("logs/execution_results.jsonl"),
]


def update_universe_from_dna_logs(universe_path: str = "runtime/discovery_universe.json") -> DiscoveryUniverse:
    universe = DiscoveryUniverse.load(universe_path)

    for path in DNA_LOG_PATHS:
        if not path.exists():
            continue

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue

            try:
                row = json.loads(line)
            except Exception:
                continue

            tokens = row.get("tokens") or row.get("route_tokens") or []
            if not isinstance(tokens, list):
                continue

            simulated_net = float(row.get("simulated_net_usd") or row.get("net_profit_usd") or 0.0)
            realized_net = row.get("realized_net_opportunity_usd")

            for token in tokens:
                if not isinstance(token, str) or not token.startswith("0x"):
                    continue
                entry = universe.tokens.get(token.lower())
                if not entry:
                    continue

                entry.candidate_count += 1
                entry.simulated_net_usd_total += simulated_net

                status = str(row.get("realized_status") or "")
                if status == "LIVE_REALIZED":
                    entry.realized_success_count += 1
                    if realized_net is not None:
                        entry.realized_net_usd_total = (entry.realized_net_usd_total or 0.0) + float(realized_net)

                if bool(row.get("fork_sim_pass")):
                    entry.fork_sim_pass_count += 1

                if str(row.get("c1_decision") or "").upper() in {"BUILD_PAYLOAD", "STRIKE", "EXECUTE"}:
                    entry.c1_strike_count += 1

                if str(row.get("c2_decision") or "").upper() == "NO_OP":
                    entry.c2_no_op_count += 1

    universe.save()
    return universe
