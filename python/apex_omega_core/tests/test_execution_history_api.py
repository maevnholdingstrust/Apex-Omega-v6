import pytest

flask = pytest.importorskip("flask")


def test_execution_history_and_trace_endpoints(monkeypatch, tmp_path):
    import app
    from apex_omega_core.core import execution_state_store as store_module

    test_store = store_module.ExecutionStateStore(tmp_path / "execution_state_history.jsonl")
    test_store.append(
        {
            "idempotency_key": "idem-api-1",
            "chain_id": 137,
            "status": "submitted",
            "tx_hash": "0xabc",
            "token_pair": "USDC/WPOL",
        }
    )
    monkeypatch.setattr(store_module, "_STORE", test_store)

    history = app.app.test_client().get("/api/execution-history?limit=10")
    assert history.status_code == 200
    payload = history.get_json()
    assert payload["count"] == 1
    assert payload["records"][0]["idempotency_key"] == "idem-api-1"
    assert payload["records"][0]["explorer_url"] == "https://polygonscan.com/tx/0xabc"

    trace = app.app.test_client().get("/api/execution-trace?limit=10")
    assert trace.status_code == 200
    trace_payload = trace.get_json()
    assert trace_payload["count"] == 1
    assert trace_payload["trace"][0]["status"] == "submitted"
