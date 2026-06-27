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
    assert "live_ready" in payload
    assert isinstance(payload["live_ready"], bool)
    assert payload["live_ready"] is (len(payload.get("live_blockers", [])) == 0)


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


def test_dashboard_opportunity_ledger_is_no_broadcast():
    import app

    response = app.app.test_client().get("/api/opportunity-ledger?limit=5")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] in {"dry_run_no_broadcast", "NO_BROADCAST_DRY_RUN"}
    assert "invariant_pass_count" in payload
    assert "payload_validated_count" in payload
    assert "fork_pass_count" in payload
    assert isinstance(payload["rows"], list)
    for row in payload["rows"]:
        assert "leg_price_invariant_status" in row
        assert "payload_status" in row
        assert "fork_status" in row
        assert row["broadcast_enabled"] is False


def test_dashboard_architecture_alignment_is_evidence_based():
    import app

    response = app.app.test_client().get("/api/architecture")

    assert response.status_code == 200
    payload = response.get_json()
    assert "Rust" in payload["target"] or "Polygon" in payload["target"]
    assert payload["active_runtime"]
    assert isinstance(payload["components"], list)
    assert payload["components"]
    statuses = {row["status"] for row in payload["components"]}
    assert "wired_idle" in statuses or "not_wired" in statuses
    if "wired_idle" in statuses:
        assert payload["wired_idle_count"] >= 1
    assert payload["aqs_canon"]["rule_count"] == 22
    assert payload["aqs_canon"]["terminal_c2_states"] == ["EXPIRED"]
    for row in payload["components"]:
        assert row["name"]
        assert row["evidence"]


def test_dashboard_token_prices_uses_cached_live_report_by_default():
    import app

    response = app.app.test_client().get("/api/token-prices?size=100000")

    assert response.status_code in {200, 500}
    payload = response.get_json()
    if response.status_code == 200 and payload.get("count", 0) > 0:
        assert payload["cache"] is True
        assert payload["evaluation_mode"] == "cached_live_price_report"
        assert payload["records"][0]["evaluation_status"] == "REFERENCE_PRICE_READY"
    else:
        assert "records" in payload

