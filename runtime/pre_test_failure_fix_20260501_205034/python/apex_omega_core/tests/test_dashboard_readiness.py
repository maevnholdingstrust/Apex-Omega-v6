def test_dashboard_health_uses_readiness_report():
    import app

    response = app.app.test_client().get("/healthz")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["modules_loaded"] == payload["modules_total"]
    assert payload["production_ready"] is True
    assert payload["ok"] is True


def test_dashboard_status_exposes_readiness_report():
    import app

    response = app.app.test_client().get("/api/status?rpc=http://127.0.0.1:1")

    assert response.status_code == 200
    payload = response.get_json()
    assert "readiness" in payload
    assert payload["readiness"]["production_ready"] is True


def test_dashboard_execution_dna_is_no_broadcast():
    import app

    response = app.app.test_client().get("/api/execution-dna?limit=2")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "NO_BROADCAST_DRY_RUN"
    assert payload["broadcast"]["enabled"] is False
    assert payload["count"] == 2
    assert payload["cards"][0]["payloads"]["c1"]["target"]
    assert payload["cards"][0]["payloads"]["c2"]["target"]
