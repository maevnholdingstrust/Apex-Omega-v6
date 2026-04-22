"""Apex-Omega-v6 dashboard server.

Serves a status / info page and exposes the live Polygon arbitrage
scanner over HTTP on port 5000.

Endpoints
---------
GET  /                  HTML dashboard (status + recent scan)
GET  /healthz           JSON health probe
GET  /api/modules       JSON module load status
GET  /api/scan?n=20     Run a live scan over Polygon (JSON results).
                        Query params:
                            n         target opportunity count (default 20)
                            size      max trade size USD (default 10000)
                            provider  flash-loan provider: balancer|aave_v3|
                                      uniswap_v3|none (default balancer)
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "python"))

app = Flask(__name__)

CORE_MODULES = [
    "apex_omega_core.core.spread_alignment",
    "apex_omega_core.core.slippage_sentinel",
    "apex_omega_core.core.inference",
    "apex_omega_core.core.feature_factory",
    "apex_omega_core.core.types",
    "apex_omega_core.core.scanner_surface",
    "apex_omega_core.core.dashboard_coordinator",
    "apex_omega_core.strategies.execution_router",
]


def _module_status():
    results = []
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
            results.append({"module": name, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append({"module": name, "ok": False, "error": str(exc)})
    return results


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Apex-Omega-v6</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #c9d1d9; margin: 0; padding: 2rem; }
  h1 { color: #58a6ff; margin-top: 0; }
  h2 { color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: .25rem; }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 500; }
  .ok { color: #3fb950; }
  .err { color: #f85149; }
  code { background: #161b22; padding: .1rem .35rem; border-radius: 4px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; }
  button { background: #238636; color: white; border: 0; border-radius: 6px;
           padding: .5rem 1rem; cursor: pointer; font-weight: 600; }
  button:disabled { background: #30363d; cursor: not-allowed; }
  pre { background: #161b22; padding: 1rem; border-radius: 6px; overflow: auto; }
</style>
</head>
<body>
  <h1>Apex-Omega-v6</h1>
  <p>Polygon (chain-id 137) cross-DEX arbitrage scanner.  Closed-form
     optimal trade sizing (Angeris-Chitra), TVL + price-sanity filtered,
     Balancer flash loans by default.</p>

  <h2>Live scan</h2>
  <div>
    <button id="run">Run scan</button>
    <span id="status" style="margin-left:1rem; color:#8b949e;"></span>
  </div>
  <pre id="out" style="margin-top:1rem;">Click "Run scan" to start a live Polygon scan.</pre>

  <h2>Core module status</h2>
  <table>
    <thead><tr><th>Module</th><th>Status</th><th>Error</th></tr></thead>
    <tbody>
    {% for row in modules %}
      <tr>
        <td><code>{{ row.module }}</code></td>
        <td class="{{ 'ok' if row.ok else 'err' }}">
          {{ 'OK' if row.ok else 'FAIL' }}
        </td>
        <td>{{ row.error or '' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <h2>Endpoints</h2>
  <div class="grid">
    <div class="card"><code>GET /</code><br/>This dashboard.</div>
    <div class="card"><code>GET /healthz</code><br/>JSON health probe.</div>
    <div class="card"><code>GET /api/modules</code><br/>JSON module status.</div>
    <div class="card"><code>GET /api/scan?n=20&amp;provider=balancer</code><br/>Run a live scan.</div>
  </div>

  <script>
    const btn = document.getElementById('run');
    const out = document.getElementById('out');
    const stat = document.getElementById('status');
    btn.onclick = async () => {
      btn.disabled = true;
      stat.textContent = "Scanning Polygon… (30-90s)";
      out.textContent = "";
      try {
        const r = await fetch('/api/scan?n=20&provider=balancer&max_scans=5&min_profit=1');
        const j = await r.json();
        out.textContent = JSON.stringify(j, null, 2);
        stat.textContent = `Done: ${j.profitable_count}/${j.records.length} profitable, ` +
                           `sum E[profit] $${(j.sum_e_profit||0).toFixed(4)}`;
      } catch (e) {
        out.textContent = "Error: " + e.message;
        stat.textContent = "";
      } finally {
        btn.disabled = false;
      }
    };
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML, modules=_module_status())


@app.route("/healthz")
def healthz():
    mods = _module_status()
    ok = all(m["ok"] for m in mods)
    return jsonify({"ok": ok, "modules_loaded": sum(1 for m in mods if m["ok"]),
                    "modules_total": len(mods)})


@app.route("/api/modules")
def api_modules():
    return jsonify(_module_status())


@app.route("/api/scan")
def api_scan():
    """Run a live Polygon scan and return JSON results.

    Query params:
        n        target opportunity count (1-100, default 20)
        size     max trade size USD (default 10_000)
        provider flash-loan provider name (default balancer)
        rpc      override Polygon RPC URL (default $POLYGON_RPC or DRPC)
    """
    try:
        n = max(1, min(100, int(request.args.get("n", "20"))))
    except ValueError:
        n = 20
    try:
        size = float(request.args.get("size", "10000"))
    except ValueError:
        size = 10_000.0
    try:
        max_scans = int(request.args.get("max_scans", "5"))
    except ValueError:
        max_scans = 5
    try:
        min_profit = float(request.args.get("min_profit", "1.0"))
    except ValueError:
        min_profit = 1.0
    provider = request.args.get("provider", "balancer")
    rpc = request.args.get("rpc") or os.getenv("POLYGON_RPC", "https://polygon.drpc.org")

    # Lazy import to keep dashboard boot fast & isolate web3 errors.
    from dry_run import run_live_opportunity_scan

    loop = asyncio.new_event_loop()
    try:
        records = loop.run_until_complete(
            run_live_opportunity_scan(
                rpc_url=rpc,
                target_count=n,
                trade_size_usd=size,
                flash_loan_provider=provider,
                min_net_profit_usd=min_profit,
                max_scans=max_scans,
            )
        )
    finally:
        loop.close()

    rec_dicts = [asdict(r) for r in records]
    profitable = [r for r in rec_dicts if r.get("profitable")]
    return jsonify({
        "rpc": rpc,
        "flash_loan_provider": provider,
        "trade_size_cap_usd": size,
        "max_scans": max_scans,
        "min_net_profit_usd": min_profit,
        "records": rec_dicts,
        "profitable_count": len(profitable),
        "sum_e_profit": sum(float(r.get("e_profit", 0.0)) for r in rec_dicts),
        "max_net_edge": max((float(r.get("expected_net_edge", 0.0)) for r in rec_dicts), default=0.0),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
