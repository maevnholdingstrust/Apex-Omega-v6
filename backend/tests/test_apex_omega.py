"""Apex Omega Final 2.0 backend regression tests."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Read from frontend env if not exported
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------------- Health & Status ----------------
class TestHealth:
    def test_health(self, client):
        r = client.get(f"{API}/health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "block" in data and isinstance(data["block"], int)

    def test_status(self, client):
        r = client.get(f"{API}/status", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["chain"] == "Polygon 137"
        assert "mode" in data
        assert isinstance(data["block"], int)
        assert isinstance(data["venues"], list) and len(data["venues"]) > 0
        assert isinstance(data["pairs"], list) and len(data["pairs"]) > 0
        assert isinstance(data["modules"], list) and len(data["modules"]) >= 5
        assert {"name", "ok"} <= set(data["modules"][0].keys())


# ---------------- Liquidity Graph ----------------
class TestGraph:
    def test_graph_pools(self, client):
        r = client.get(f"{API}/graph", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["block"], int)
        pools = data["pools"]
        assert isinstance(pools, list) and len(pools) > 10
        first = pools[0]
        for k in ("pool_id", "venue", "family", "fee", "base", "quote",
                  "reserve_base", "reserve_quote", "price", "tvl_usd", "usd_per_base"):
            assert k in first, f"missing {k} in pool"


# ---------------- Discovery & Opportunities ----------------
class TestDiscovery:
    def test_scan_returns_opportunities(self, client):
        r = client.post(f"{API}/discovery/scan",
                        json={"trade_size_usd": 12000, "min_spread_bps": 8.0},
                        timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "count" in data
        assert isinstance(data["opportunities"], list)
        # Stash an opp id for next tests on the session
        pytest._opps = data["opportunities"]

    def test_opportunity_fields(self, client):
        opps = getattr(pytest, "_opps", None)
        if not opps:
            # Force a scan with a permissive threshold
            r = client.post(f"{API}/discovery/scan",
                            json={"trade_size_usd": 12000, "min_spread_bps": 0.0},
                            timeout=15)
            opps = r.json()["opportunities"]
            pytest._opps = opps
        assert len(opps) > 0, "Expected at least one opportunity"
        o = opps[0]
        required = ["opp_id", "pair", "raw_spread_bps", "net_profit_usd",
                    "selected_ev_usdc", "ev_curve", "executable",
                    "buy_venue", "sell_venue", "buy_family", "sell_family",
                    "buy_price_usd", "sell_price_usd", "selected_buffer", "block"]
        for k in required:
            assert k in o, f"missing field {k}"
        assert isinstance(o["ev_curve"], list) and len(o["ev_curve"]) == 6
        for pt in o["ev_curve"]:
            assert {"multiplier", "buffer", "ev"} <= set(pt.keys())

    def test_list_opportunities(self, client):
        r = client.get(f"{API}/opportunities?limit=10", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["opportunities"], list)
        assert data["count"] == len(data["opportunities"])


# ---------------- Risk ----------------
class TestRisk:
    def test_risk_for_known_opp(self, client):
        opps = getattr(pytest, "_opps", None) or client.get(f"{API}/opportunities?limit=1").json()["opportunities"]
        assert opps, "No opportunities available"
        opp_id = opps[0]["opp_id"]
        r = client.get(f"{API}/risk/{opp_id}", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in ("route_fragility", "state_divergence", "revert_probability",
                  "mempool_toxicity", "liquidity_saturation",
                  "v3_tick_confidence", "overall_risk_score"):
            assert k in d
            assert isinstance(d[k], (int, float))

    def test_risk_404(self, client):
        r = client.get(f"{API}/risk/opp_doesnotexist", timeout=10)
        assert r.status_code == 404


# ---------------- Pipeline run ----------------
class TestPipeline:
    def test_pipeline_full(self, client):
        opps = getattr(pytest, "_opps", None) or client.get(f"{API}/opportunities?limit=1").json()["opportunities"]
        assert opps, "No opportunities"
        opp_id = opps[0]["opp_id"]
        r = client.post(f"{API}/pipeline/run", json={"opp_id": opp_id}, timeout=20)
        assert r.status_code == 200, r.text
        cyc = r.json()
        for k in ("cycle_id", "opp_id", "pair", "trade_size_usd", "risk",
                  "c1", "c2", "c1_net_profit_usd", "c2_net_profit_usd",
                  "total_net_profit_usd", "status", "created_at"):
            assert k in cyc, f"missing cycle field {k}"
        # c1 envelope + mirror checks
        c1 = cyc["c1"]
        assert "envelope" in c1 and "steps" in c1["envelope"]
        assert "mirror" in c1 and "checks" in c1["mirror"]
        # c2
        c2 = cyc["c2"]
        assert c2["action"] in ("MIRROR", "REVERSE", "DO_NOTHING")
        assert "merkle_root" in c2
        # candidates: must be exactly 3 when C1 executed; may be 0 otherwise
        if c1.get("status") == "executed":
            assert len(c2["candidates"]) == 3
            actions = {c["action"] for c in c2["candidates"]}
            assert actions == {"MIRROR", "REVERSE", "DO_NOTHING"}
            for c in c2["candidates"]:
                assert "merkle_leaf" in c and "proof" in c
                assert isinstance(c["proof"], list)
            assert "selected" in c2 and "proof" in c2["selected"]
        pytest._cycle_id = cyc["cycle_id"]

    def test_pipeline_404(self, client):
        r = client.post(f"{API}/pipeline/run", json={"opp_id": "opp_nope"}, timeout=10)
        assert r.status_code == 404


# ---------------- Cycles & Telemetry ----------------
class TestCyclesAndTelemetry:
    def test_list_cycles(self, client):
        r = client.get(f"{API}/cycles?limit=10", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["cycles"], list)
        assert d["count"] == len(d["cycles"])
        assert d["count"] >= 1

    def test_cycle_detail(self, client):
        cid = getattr(pytest, "_cycle_id", None)
        if not cid:
            cs = client.get(f"{API}/cycles?limit=1").json()["cycles"]
            cid = cs[0]["cycle_id"] if cs else None
        assert cid, "No cycle id available"
        r = client.get(f"{API}/cycles/{cid}", timeout=10)
        assert r.status_code == 200
        assert r.json()["cycle_id"] == cid

    def test_cycle_detail_404(self, client):
        r = client.get(f"{API}/cycles/cyc_notfound", timeout=10)
        assert r.status_code == 404

    def test_telemetry(self, client):
        r = client.get(f"{API}/telemetry", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in ("cycles_total", "total_profit", "c1_profit", "c2_profit",
                  "mirror_count", "reverse_count", "do_nothing_count", "block"):
            assert k in d, f"missing telemetry field {k}"
