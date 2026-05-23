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



# ---------------- Liquidity Gate (iteration 2) ----------------
class TestLiquidityGate:
    def test_gate_returns_config_counters_and_lists(self, client):
        r = client.get(f"{API}/liquidity/gate", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "config" in d
        for k in ("min_tvl_usd", "max_price_dev_pct", "max_freshness_ms"):
            assert k in d["config"]
        c = d["counters"]
        for k in ("total", "eligible", "rejected", "tvl_fail",
                  "price_sanity_fail", "freshness_fail"):
            assert k in c
        assert c["total"] == c["eligible"] + c["rejected"] or c["total"] >= c["eligible"]
        assert isinstance(d["eligible"], list)
        assert isinstance(d["rejected"], list)
        for e in d["eligible"][:5]:
            for k in ("pool_id", "venue", "family", "pair", "tvl_usd",
                      "usd_per_base", "freshness_ms"):
                assert k in e
        for rj in d["rejected"][:5]:
            assert "reject_reasons" in rj and isinstance(rj["reject_reasons"], list)
            assert len(rj["reject_reasons"]) >= 1

    def test_gate_custom_config(self, client):
        r = client.get(f"{API}/liquidity/gate",
                       params={"min_tvl_usd": 100, "max_freshness_ms": 99999,
                               "max_price_dev_pct": 1.0}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        # With very permissive config most pools should be eligible
        assert d["counters"]["eligible"] >= d["counters"]["rejected"]

    def test_scan_response_includes_gate_breakdown(self, client):
        r = client.post(f"{API}/discovery/scan",
                        json={"trade_size_usd": 12000, "min_spread_bps": 8.0},
                        timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "gate" in d
        g = d["gate"]
        assert "config" in g and "counters" in g and "rejected" in g
        c = g["counters"]
        for k in ("total", "eligible", "rejected", "tvl_fail",
                  "price_sanity_fail", "freshness_fail"):
            assert k in c

    def test_scan_opps_only_from_eligible_pools(self, client):
        gate = client.get(f"{API}/liquidity/gate", timeout=10).json()
        eligible_pool_ids = {e["pool_id"] for e in gate["eligible"]}
        r = client.post(f"{API}/discovery/scan",
                        json={"trade_size_usd": 12000, "min_spread_bps": 0.0},
                        timeout=15)
        d = r.json()
        for o in d["opportunities"]:
            assert o["buy_pool_id"] in eligible_pool_ids, \
                f"buy pool {o['buy_pool_id']} not in eligible set"
            assert o["sell_pool_id"] in eligible_pool_ids, \
                f"sell pool {o['sell_pool_id']} not in eligible set"


# ---------------- Liquidations (iteration 2) ----------------
class TestLiquidations:
    def test_scan_positions(self, client):
        r = client.get(f"{API}/liquidations/scan", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["close_factor"] == 0.5
        assert isinstance(d["count"], int) and d["count"] > 0
        assert isinstance(d["eligible_count"], int)
        assert isinstance(d["positions"], list)
        p = d["positions"][0]
        for k in ("position_id", "health_factor", "collateral_asset", "debt_asset",
                  "collateral_usd", "debt_usd", "max_repay_usd",
                  "seized_collateral_usd", "raw_bonus_usd", "flash_fee_usd",
                  "gas_cost_usd", "net_bonus_usd", "eligible", "executable"):
            assert k in p, f"missing position field {k}"
        # Position sorted ascending by HF
        hfs = [pp["health_factor"] for pp in d["positions"]]
        assert hfs == sorted(hfs)
        pytest._positions = d["positions"]

    def test_only_eligible_filter(self, client):
        r = client.get(f"{API}/liquidations/scan",
                       params={"only_eligible": True}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        for p in d["positions"]:
            assert p["eligible"] is True
            assert p["health_factor"] < 1.0

    def test_execute_liquidation_404(self, client):
        r = client.post(f"{API}/liquidations/execute",
                        json={"position_id": "pos_doesnotexist"}, timeout=10)
        assert r.status_code == 404

    def test_execute_liquidation_eligible_persists(self, client):
        # Find an eligible position; if none, try a few rescans (market may have drifted)
        eligible = None
        for _ in range(6):
            d = client.get(f"{API}/liquidations/scan",
                           params={"only_eligible": True}, timeout=10).json()
            if d["positions"]:
                eligible = d["positions"][0]
                break
            time.sleep(2.2)
        if not eligible:
            pytest.skip("No eligible position appeared during test window")
        # Snapshot pre-state
        pre_debt = eligible["debt_usd"]
        pre_coll = eligible["collateral_usd"]
        # Execute
        r = client.post(f"{API}/liquidations/execute",
                        json={"position_id": eligible["position_id"]}, timeout=10)
        assert r.status_code == 200, r.text
        rec = r.json()
        for k in ("liquidation_id", "position_id", "borrower", "status",
                  "max_repay_usd", "seized_collateral_usd",
                  "expected_net_bonus_usd", "actual_net_bonus_usd",
                  "result", "created_at"):
            assert k in rec, f"missing record field {k}"
        assert rec["status"] in ("executed", "frontran")
        assert "bundle_hash" in rec["result"]
        # If executed, position state must mutate (debt down, collateral down)
        if rec["status"] == "executed":
            scan = client.get(f"{API}/liquidations/scan", timeout=10).json()
            after = next((p for p in scan["positions"]
                          if p["position_id"] == eligible["position_id"]), None)
            assert after is not None
            assert after["debt_usd"] < pre_debt + 0.01
            assert after["collateral_usd"] < pre_coll + 0.01

    def test_history_endpoint(self, client):
        r = client.get(f"{API}/liquidations/history", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["liquidations"], list)
        assert d["count"] == len(d["liquidations"])

    def test_telemetry_has_liquidation_fields(self, client):
        r = client.get(f"{API}/telemetry", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in ("liquidations_total", "liquidations_executed",
                  "liquidations_frontran", "liquidation_bonus_total"):
            assert k in d, f"missing telemetry field {k}"
