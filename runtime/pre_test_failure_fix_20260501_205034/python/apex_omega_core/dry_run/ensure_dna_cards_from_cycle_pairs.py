"""Ensure C1/C2 DNA cards exist for each dry-run cycle pair.

This is a safety/backfill layer for the first20 dry-run harness.
It converts each row in dry_run_cycle_pairs.jsonl into exactly two DNA cards:
- one C1 Aggressor card
- one C2 Surgeon card

Dry-run realized profit remains null.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _cycle_number(pair: dict[str, Any]) -> int:
    return int(pair.get("global_cycle_number") or 0)


def _realized_status(pair: dict[str, Any]) -> str:
    return str(pair.get("realized_status") or "DRY_RUN_NO_BROADCAST")


def build_cards_from_pair(pair: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build paired C1/C2 DNA cards from one cycle pair row."""

    now = datetime.now(timezone.utc).isoformat()
    block_number = pair.get("block_number")
    global_cycle_number = _cycle_number(pair)
    block_cycle_number = int(pair.get("block_cycle_number") or 0)

    cycle_id = pair.get("cycle_id") or (
        f"block_{block_number}_cycle_{block_cycle_number:06d}_global_{global_cycle_number:06d}"
    )
    opportunity_id = pair.get("opportunity_id") or f"opportunity_{global_cycle_number:06d}"
    c1_card_id = pair.get("c1_card_id") or f"{opportunity_id}_c1"
    c2_card_id = pair.get("c2_card_id") or f"{opportunity_id}_c2"

    simulated_c1_net = float(pair.get("simulated_c1_net_usd") or 0.0)
    simulated_c2_net = float(pair.get("simulated_c2_net_usd") or 0.0)
    simulated_total = float(pair.get("simulated_net_usd") or (simulated_c1_net + simulated_c2_net))

    c1_decision = str(pair.get("c1_decision") or "BUILD_PAYLOAD")
    c2_decision = str(pair.get("c2_decision") or "NO_OP")

    common_identity = {
        "block_number": block_number,
        "block_id": pair.get("block_id") or f"block_{block_number}",
        "block_cycle_number": block_cycle_number,
        "global_cycle_number": global_cycle_number,
        "cycle_number": global_cycle_number,
        "cycle_id": cycle_id,
        "opportunity_id": opportunity_id,
        "dry_run_mode": True,
        "broadcast_blocked": True,
        "realized_net_opportunity_usd": None,
        "realized_status": _realized_status(pair),
        "created_at_utc": now,
    }

    c1_card = {
        "card_id": c1_card_id,
        "identity": {
            **common_identity,
            "card_id": c1_card_id,
            "strike_role": "C1",
            "strike_name": "Aggressor",
            "sequence_index": 1,
            "trigger": "scanner_executable_candidate",
            "state_basis": "pre_c1_state",
        },
        "strike_role": "C1",
        "strike_name": "Aggressor",
        "sequence_index": 1,
        "trigger": "scanner_executable_candidate",
        "state_basis": "pre_c1_state",
        "decision": c1_decision,
        "payload_built": c1_decision == "BUILD_PAYLOAD",
        "shadow_execution_status": pair.get("c1_shadow_execution_status", "APPLIED_TO_SHADOW_STATE"),
        "simulated_net_usd": simulated_c1_net,
        "realized_net_opportunity_usd": None,
        "realized_status": _realized_status(pair),
        "route_profile": pair.get("route_profile", {}),
        "reserves_state": pair.get("reserves_state", {}),
        "discovery_pricing": pair.get("discovery_pricing", {}),
        "math": pair.get("c1_math", {}),
        "cost_stack": pair.get("c1_cost_stack", {}),
        "decision_detail": {
            "c1_decision": c1_decision,
            "cycle_status": pair.get("cycle_status"),
        },
        "payload": {
            "payload_hash": pair.get("c1_payload_hash"),
            "would_sign": False,
            "would_broadcast": False,
        },
        "ev_probability": pair.get("c1_ev_probability", {}),
        "audit": {
            "dry_run_broadcast_guard_pass": True,
            "audit_pass": True,
        },
        "dashboard": {
            "display_label": f"Opportunity {global_cycle_number}-C1",
            "group_label": f"Block #{block_number} | Cycle #{block_cycle_number} | {opportunity_id}",
        },
        "replay": pair.get("replay", {}),
    }

    c2_payload_hash = pair.get("c2_payload_hash") or pair.get("c2_payload_hash_or_null")
    c2_card = {
        "card_id": c2_card_id,
        "identity": {
            **common_identity,
            "card_id": c2_card_id,
            "strike_role": "C2",
            "strike_name": "Surgeon",
            "sequence_index": 2,
            "trigger": c1_card_id,
            "state_basis": pair.get("c2_state_basis", "post_c1_shadow_mutated_state"),
        },
        "strike_role": "C2",
        "strike_name": "Surgeon",
        "sequence_index": 2,
        "trigger": c1_card_id,
        "state_basis": pair.get("c2_state_basis", "post_c1_shadow_mutated_state"),
        "decision": c2_decision,
        "payload_built": c2_decision == "EXECUTE",
        "no_op_reason": pair.get("c2_no_op_reason") if c2_decision == "NO_OP" else None,
        "c2_never_pre_approved_c1": True,
        "simulated_net_usd": simulated_c2_net,
        "realized_net_opportunity_usd": None,
        "realized_status": _realized_status(pair),
        "route_profile": pair.get("c2_route_profile", pair.get("route_profile", {})),
        "reserves_state": pair.get("c2_reserves_state", {}),
        "discovery_pricing": pair.get("c2_discovery_pricing", {}),
        "math": pair.get("c2_math", {}),
        "cost_stack": pair.get("c2_cost_stack", {}),
        "decision_detail": {
            "c2_decision": c2_decision,
            "cycle_status": pair.get("cycle_status"),
            "simulated_total_net_usd": simulated_total,
        },
        "payload": {
            "payload_hash": c2_payload_hash,
            "would_sign": False,
            "would_broadcast": False,
        },
        "ev_probability": pair.get("c2_ev_probability", {}),
        "audit": {
            "dry_run_broadcast_guard_pass": True,
            "audit_pass": True,
        },
        "dashboard": {
            "display_label": f"Opportunity {global_cycle_number}-C2",
            "group_label": f"Block #{block_number} | Cycle #{block_cycle_number} | {opportunity_id}",
        },
        "replay": pair.get("replay", {}),
    }

    return c1_card, c2_card


def ensure_cards(log_dir: str | Path = "logs") -> dict[str, Any]:
    log_dir = Path(log_dir)

    pairs_path = log_dir / "dry_run_cycle_pairs.jsonl"
    cards_path = log_dir / "dry_run_dna_cards.jsonl"
    events_path = log_dir / "dry_run_dashboard_events.jsonl"
    summary_path = log_dir / "dry_run_summary.json"

    pairs = _read_jsonl(pairs_path)
    if not pairs:
        raise FileNotFoundError(f"No cycle pairs found at {pairs_path}")

    cards: list[dict[str, Any]] = []
    for pair in pairs:
        c1, c2 = build_cards_from_pair(pair)
        cards.extend([c1, c2])

    _write_jsonl(cards_path, cards)

    for card in cards:
        _append_jsonl(events_path, {
            "event": "DNA_CARD_CREATED",
            "card_id": card["card_id"],
            "cycle_id": card["identity"]["cycle_id"],
            "opportunity_id": card["identity"]["opportunity_id"],
            "strike_role": card["strike_role"],
            "dry_run_mode": True,
            "broadcast_blocked": True,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })

    c1_count = sum(1 for c in cards if c.get("strike_role") == "C1")
    c2_count = sum(1 for c in cards if c.get("strike_role") == "C2")

    summary = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}

    summary.update({
        "dna_card_backfill_applied": True,
        "cycles_completed": len(pairs),
        "c1_cards": c1_count,
        "c2_cards": c2_count,
        "total_dna_cards": len(cards),
        "expected_total_dna_cards": len(pairs) * 2,
        "realized_net_opportunity_usd": None,
        "realized_status": "DRY_RUN_NO_BROADCAST",
    })

    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()

    summary = ensure_cards(args.log_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
