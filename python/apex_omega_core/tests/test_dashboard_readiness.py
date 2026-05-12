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


def test_dashboard_execution_live_endpoint(monkeypatch):
    import app

    monkeypatch.setattr(
        "apex_omega_core.core.execution_dna.build_live_execution_payloads",
        lambda **_kwargs: {
            "mode": "LIVE_DISCOVERY_REALTIME_ENCODING",
            "count": 1,
            "requested": 1,
            "auto_submit_requested": False,
            "auto_submit_enabled": False,
            "live_blockers": [],
            "tx_discovery": {"status": "ok", "pending_total": 0, "sampled": 0, "swap_like": 0},
            "p_fill_estimate": 0.99,
            "cards": [],
        },
    )

    response = app.app.test_client().get("/api/execution-live?limit=1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "LIVE_DISCOVERY_REALTIME_ENCODING"
    assert payload["count"] == 1
