import json
from pathlib import Path

from apex_omega_core.dry_run.dry_run_orchestrator import DryRunOrchestrator
from apex_omega_core.safety.dry_run_guard import enforce_no_broadcast_env, assert_no_broadcast, DryRunBroadcastBlockedError


def _count_lines(path: Path) -> int:
    return sum(1 for _ in path.open()) if path.exists() else 0


def test_first20_means_20_c1_cycles_and_40_cards(tmp_path: Path) -> None:
    o = DryRunOrchestrator(log_dir=str(tmp_path))
    summary = o.run(limit=20)
    assert summary['completed_cycles'] == 20
    assert _count_lines(tmp_path / 'dry_run_dna_cards.jsonl') == 40


def test_c2_card_exists_when_no_op(tmp_path: Path) -> None:
    o = DryRunOrchestrator(log_dir=str(tmp_path))
    o.run(limit=2)
    rows = [json.loads(x) for x in (tmp_path / 'dry_run_dna_cards.jsonl').read_text().splitlines()]
    assert any(r['identity']['strike_role'] == 'C2' and r['identity']['decision'] == 'NO_OP' for r in rows)


def test_global_cycle_monotonic_and_cycle_id_shape(tmp_path: Path) -> None:
    o = DryRunOrchestrator(log_dir=str(tmp_path))
    o.run(limit=5)
    pairs = [json.loads(x) for x in (tmp_path / 'dry_run_cycle_pairs.jsonl').read_text().splitlines()]
    nums = [p['global_cycle_number'] for p in pairs]
    assert nums == sorted(nums)
    assert all('block_' in p['cycle_id'] and '_global_' in p['cycle_id'] for p in pairs)


def test_dry_run_realized_net_is_null(tmp_path: Path) -> None:
    o = DryRunOrchestrator(log_dir=str(tmp_path))
    o.run(limit=3)
    pairs = [json.loads(x) for x in (tmp_path / 'dry_run_cycle_pairs.jsonl').read_text().splitlines()]
    assert all(p['realized_net_opportunity_usd'] is None for p in pairs)


def test_broadcast_attempt_blocked_in_dry_run() -> None:
    enforce_no_broadcast_env()
    try:
        assert_no_broadcast('eth_sendRawTransaction')
        assert False
    except DryRunBroadcastBlockedError:
        assert True
