"""Apex-Omega-v6 minimal dashboard server.

Serves a simple status / info page on port 5000 so the project has a
runnable web entry point inside the Replit environment. The underlying
trading library lives under ``python/apex_omega_core``; this app surfaces
basic health and module availability for visibility.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template_string

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
</style>
</head>
<body>
  <h1>Apex-Omega-v6</h1>
  <p>High-performance arbitrage / trading system. This page reports the
     health of the Python core modules in the running environment.</p>

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
  </div>

  <h2>Next steps</h2>
  <ul>
    <li>Run tests: <code>pytest python/apex_omega_core/tests/</code></li>
    <li>Dry run: <code>python python/dry_run.py</code></li>
  </ul>
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
