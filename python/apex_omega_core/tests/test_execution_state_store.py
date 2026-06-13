from __future__ import annotations

from apex_omega_core.core.execution_state_store import ExecutionStateStore, explorer_url_for


def test_explorer_url_for_polygon():
    tx_hash = "0xabc123"
    assert explorer_url_for(137, tx_hash) == f"https://polygonscan.com/tx/{tx_hash}"


def test_execution_state_store_normalizes_required_fields(tmp_path):
    store = ExecutionStateStore(tmp_path / "events.jsonl")
    event = store.append(
        {
            "idempotency_key": "idem-1",
            "chain_id": 137,
            "status": "submitted",
            "tx_hash": "0xdeadbeef",
            "cycle_id": "7-C1-C2",
            "lane_id": 3,
            "strike_role": "C1",
            "c2_decision": "MIRROR",
            "submission_transport": "polygon_private_mempool",
            "expected_profit_usd": 12.5,
            "realized_profit_usd": 11.75,
        }
    )
    assert event["chain_name"] == "Polygon"
    assert event["status"] == "submitted"
    assert event["explorer_url"] == "https://polygonscan.com/tx/0xdeadbeef"
    assert event["cycle_id"] == "7-C1-C2"
    assert event["lane_id"] == 3
    assert event["submission_transport"] == "polygon_private_mempool"
    assert event["realized_profit_usd"] == 11.75
    assert isinstance(event["rejection_reasons"], list)
    assert store.list_recent(limit=1)[0]["idempotency_key"] == "idem-1"
