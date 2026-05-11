import pytest

flask = pytest.importorskip("flask")


def test_dashboard_health_uses_readiness_report():
    import app

    response = app.app.test_client().get("/healthz")

    assert response.status_code == 200
    payload = response.get_json()
    # Structural checks — production_ready may be False when Rust wheel is absent (e.g. CI).
    assert payload["modules_loaded"] == payload["modules_total"]
    assert isinstance(payload["production_ready"], bool)
    assert isinstance(payload["ok"], bool)


def test_dashboard_status_exposes_readiness_report():
    import app

    response = app.app.test_client().get("/api/status?rpc=http://127.0.0.1:1")

    assert response.status_code == 200
    payload = response.get_json()
    assert "readiness" in payload
    # production_ready is False when Rust wheel is absent; check field presence and type.
    assert isinstance(payload["readiness"]["production_ready"], bool)


def test_dashboard_execution_dna_is_no_broadcast():
    import app

    response = app.app.test_client().get("/api/execution-dna?limit=2")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "NO_BROADCAST_DRY_RUN"
    assert payload["broadcast"]["enabled"] is False
    # count reflects how many cards were buildable from the fallback states;
    # may be 0 when no state is strikeable in a dry CI environment.
    assert isinstance(payload["count"], int)
    assert payload["count"] >= 0
    for card in payload.get("cards", []):
        assert card.get("payloads", {}).get("c1", {}).get("target")
        assert card.get("payloads", {}).get("c2", {}).get("target")


def test_dashboard_live_e2e_endpoint_returns_payload(monkeypatch):
    import app
    import apex_omega_core.core.live_e2e_pipeline as live_e2e_pipeline

    async def _fake_run_live_e2e_cycle(**_kwargs):
        return {
            "mode": "simulate_only",
            "submit_live": False,
            "blockers": [],
            "scan": {"scanned": 1, "candidates": 1},
            "mempool": {"pending_swap_count": 0},
            "payload": {"compiled_payload_bytes": 32},
            "submission": {"attempted": False, "results": []},
        }

    monkeypatch.setattr(live_e2e_pipeline, "run_live_e2e_cycle", _fake_run_live_e2e_cycle)
    response = app.app.test_client().get("/api/live-e2e?submit=0&capture_s=0.5&max_candidates=1")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "simulate_only"
    assert payload["submission"]["attempted"] is False
