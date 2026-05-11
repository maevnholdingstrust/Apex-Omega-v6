import json
from pathlib import Path

from apex_omega_core.dry_run.block_cycle_index import reset_block_cycle_index
from apex_omega_core.dry_run.dna_logger import reset_dry_run_logger
from apex_omega_core.dry_run.dry_run_orchestrator import DryRunOrchestrator
from apex_omega_core.dry_run.realtime_bus import reset_realtime_bus
from apex_omega_core.safety.dry_run_guard import (
    DryRunBroadcastBlockedError,
    assert_no_broadcast,
    enforce_no_broadcast_env,
)


def _count_lines(path: Path) -> int:
    return sum(1 for _ in path.open()) if path.exists() else 0


def _live_candidate(i: int, block: int = 86283698) -> dict:
    return {
        "live_data": True,
        "source": "test_live_snapshot",
        "block_number": block,
        "estimated_profit_usd": 10.0 + i,
        "post_c1_estimated_profit_usd": 1.0 if i % 2 else 0.0,
    }


def _orchestrator(tmp_path: Path, limit: int = 20) -> DryRunOrchestrator:
    enforce_no_broadcast_env()
    reset_dry_run_logger()
    reset_block_cycle_index()
    reset_realtime_bus()
    candidates = [_live_candidate(i) for i in range(limit)]
    return DryRunOrchestrator(
        log_dir=str(tmp_path),
        scanner_fn=lambda: candidates,
        c1_fn=lambda c: {
            "accepted": bool(c.get("live_data")) and c["estimated_profit_usd"] > 0,
            "simulated_net_usd": c["estimated_profit_usd"],
            "payload_built": True,
        },
        c2_fn=lambda c: {
            "action": "EXECUTE" if c["post_c1_estimated_profit_usd"] > 0 else "NO_OP",
            "simulated_net_usd": c["post_c1_estimated_profit_usd"],
        },
    )


def test_run_requires_live_scanner(tmp_path: Path) -> None:
    enforce_no_broadcast_env()
    reset_dry_run_logger()
    reset_block_cycle_index()
    reset_realtime_bus()
    o = DryRunOrchestrator(log_dir=str(tmp_path))
    try:
        o.run(limit=1)
        assert False
    except RuntimeError as exc:
        assert "LIVE_DATA_SCANNER_REQUIRED" in str(exc)


def test_first20_means_20_c1_cycles_and_40_cards(tmp_path: Path) -> None:
    o = _orchestrator(tmp_path, 20)
    summary = o.run(limit=20)
    assert summary["completed_cycles"] == 20
    assert _count_lines(tmp_path / "dry_run_dna_cards.jsonl") == 40


def test_c2_card_exists_when_no_op(tmp_path: Path) -> None:
    o = _orchestrator(tmp_path, 2)
    o.run(limit=2)
    rows = [
        json.loads(x)
        for x in (tmp_path / "dry_run_dna_cards.jsonl").read_text().splitlines()
    ]
    assert any(
        r["identity"]["strike_role"] == "C2"
        and r["identity"]["decision"] == "NO_OP"
        for r in rows
    )


def test_global_cycle_monotonic_and_cycle_id_shape(tmp_path: Path) -> None:
    o = _orchestrator(tmp_path, 5)
    o.run(limit=5)
    pairs = [
        json.loads(x)
        for x in (tmp_path / "dry_run_cycle_pairs.jsonl").read_text().splitlines()
    ]
    nums = [p["global_cycle_number"] for p in pairs]
    assert nums == sorted(nums)
    assert all("block_" in p["cycle_id"] and "_global_" in p["cycle_id"] for p in pairs)


def test_dry_run_realized_net_is_null(tmp_path: Path) -> None:
    o = _orchestrator(tmp_path, 3)
    o.run(limit=3)
    pairs = [
        json.loads(x)
        for x in (tmp_path / "dry_run_cycle_pairs.jsonl").read_text().splitlines()
    ]
    assert all(p["realized_net_opportunity_usd"] is None for p in pairs)


def test_broadcast_attempt_blocked_in_dry_run() -> None:
    enforce_no_broadcast_env()
    try:
        assert_no_broadcast("eth_sendRawTransaction")
        assert False
    except DryRunBroadcastBlockedError:
        assert True
