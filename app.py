"""Apex-Omega-v6 dashboard server.

Serves a real-time dashboard and exposes the live Polygon arbitrage
scanner + SSOT pipeline over HTTP on port 5000.

Endpoints
---------
GET  /                          HTML dashboard
GET  /healthz                   JSON health probe
GET  /api/modules               JSON module load status
GET  /api/status                JSON system status (Rust core, chain, env)
GET  /api/scan?n=20             Run a scan and return JSON results
GET  /api/scan/stream           Server-Sent Events: streaming scan feed
GET  /api/pipeline              Run SSOTPipelineFinalizer on pool params
GET  /api/routes                Last dry-run route records with math deltas
GET  /api/token-prices          Live venue token executable/direct prices
GET  /api/results               Last dry-run CSV as JSON records
"""

from __future__ import annotations

import asyncio
import csv
import importlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "python"))

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORE_MODULES = [
    "apex_omega_core.core.spread_alignment",
    "apex_omega_core.core.slippage_sentinel",
    "apex_omega_core.core.inference",
    "apex_omega_core.core.feature_factory",
    "apex_omega_core.core.types",
    "apex_omega_core.core.scanner_surface",
    "apex_omega_core.core.dashboard_coordinator",
    "apex_omega_core.core.ssot_pipeline",
    "apex_omega_core.core.readiness_report",
    "apex_omega_core.strategies.execution_router",
]

_DEFAULT_RPC = "https://polygon.drpc.org"
_RESULTS_CSV = ROOT / "dry_run_results.csv"


def _load_env_files() -> None:
    """Load repo env files for the dashboard without overriding shell values."""
    loaded: Dict[str, str] = {}
    for env_path in (ROOT / ".env", ROOT / "python" / "apex_omega_core" / ".env"):
        if not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                for source in (loaded, os.environ):
                    for source_key, source_value in source.items():
                        value = value.replace(f"${{{source_key}}}", source_value)
                if key:
                    loaded[key] = value
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_env_files()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_error(exc: Exception) -> str:
    """Return a sanitized, truncated error message safe for API responses.

    Strips filesystem paths and limits length to avoid leaking internal
    details (stack frames, absolute paths, credentials) to callers.
    """
    import re  # noqa: PLC0415
    msg = type(exc).__name__ + ": " + str(exc)
    # Remove absolute file-system paths (e.g. /home/runner/... or /usr/...)
    msg = re.sub(r"(/[\w./-]+)+", "<path>", msg)
    return msg[:300]


def _module_status() -> List[Dict[str, Any]]:
    results = []
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
            results.append({"module": name, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append({"module": name, "ok": False, "error": _safe_error(exc)})
    return results


def _rust_status() -> Dict[str, Any]:
    try:
        rc = importlib.import_module("apex_omega_core_rust")
        fns = sorted(f for f in dir(rc) if not f.startswith("_"))
        return {"available": True, "functions": fns, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "functions": [], "error": _safe_error(exc)}


def _chain_status(rpc: str) -> Dict[str, Any]:
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
        connected = w3.is_connected()
        block = int(w3.eth.block_number) if connected else None
        return {"connected": connected, "rpc": rpc, "block_number": block, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "rpc": rpc, "block_number": None, "error": _safe_error(exc)}


def _readiness_status() -> Dict[str, Any]:
    try:
        from apex_omega_core.core.readiness_report import build_readiness_report

        return build_readiness_report().as_dict()
    except Exception as exc:  # noqa: BLE001
        return {
            "production_ready": False,
            "components": [
                {
                    "name": "readiness_report",
                    "ok": False,
                    "detail": _safe_error(exc),
                }
            ],
            "missing_live_env": [],
            "chain_id": None,
            "dry_run": None,
            "live_trading_enabled": None,
        }


def _sse_event(data: Any, event: Optional[str] = None) -> str:
    payload = json.dumps(data) if not isinstance(data, str) else data
    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {payload}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _typed_csv_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            typed: Dict[str, Any] = {}
            for k, v in row.items():
                if v is None or v == "":
                    typed[k] = v
                    continue
                try:
                    typed[k] = float(v) if any(ch in v for ch in ".eE") else int(v)
                except (ValueError, TypeError):
                    typed[k] = v
            rows.append(typed)
    return rows


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = r"""<!doctype html>
<head>
<meta charset="utf-8" />
<title>Apex-Omega-v6 Dashboard</title>
<style>
  :root {
    --bg:       #0d1117; --surface: #161b22; --border: #30363d;
    --text:     #c9d1d9; --muted:   #8b949e;
    --green:    #3fb950; --red:     #f85149; --blue:   #58a6ff;
    --yellow:   #d29922; --purple:  #a371f7;
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem; }
  h1  { color: var(--blue); margin-top: 0; display: flex; align-items: center; gap: .75rem; }
  h2  { color: var(--muted); border-bottom: 1px solid var(--border);
        padding-bottom: .3rem; margin-top: 1.5rem; font-size: 1rem; text-transform: uppercase;
        letter-spacing: .06em; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--border); }
  th  { color: var(--muted); font-weight: 500; white-space: nowrap; }
  .ok    { color: var(--green); }
  .warn  { color: var(--yellow); }
  .err   { color: var(--red); }
  code   { background: var(--surface); padding: .1rem .3rem; border-radius: 3px;
           font-size: .82rem; }
  .badge { display: inline-block; padding: .15rem .45rem; border-radius: 12px;
           font-size: .75rem; font-weight: 600; }
  .badge-ok   { background: #1f4c2c; color: var(--green); }
  .badge-fail { background: #4c1f1f; color: var(--red); }
  .badge-rust { background: #2d1b4e; color: var(--purple); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; padding: 1rem; }
  .card-title { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em;
                color: var(--muted); margin-bottom: .4rem; }
  .card-value { font-size: 1.6rem; font-weight: 700; }
  button { background: #238636; color: #fff; border: 0; border-radius: 6px;
           padding: .4rem .9rem; cursor: pointer; font-weight: 600; font-size: .85rem; }
  button:disabled { background: var(--border); cursor: not-allowed; }
  button.secondary { background: #1c2a3a; color: var(--blue); border: 1px solid var(--border); }
  .controls { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; margin-bottom: .75rem; }
  select, input[type=number] {
    background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 4px; padding: .3rem .5rem; font-size: .85rem; }
  label { font-size: .82rem; color: var(--muted); display: flex; align-items: center; gap: .35rem; }
  pre  { background: var(--surface); padding: .75rem 1rem; border-radius: 6px;
         overflow: auto; font-size: .8rem; max-height: 340px; margin: 0; }
  #opp-tbody tr:nth-child(even) { background: #0f1419; }
  .profit-pos { color: var(--green); }
  .profit-neg { color: var(--red); }
  #stream-status { font-size: .82rem; color: var(--muted); }
  .spinner { display: inline-block; width: 10px; height: 10px; border: 2px solid var(--muted);
             border-top-color: var(--blue); border-radius: 50%; animation: spin .6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #pipeline-out { margin-top: .5rem; }
  .pipeline-row { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: .4rem; }
  .pipeline-kv  { font-size: .82rem; }
  .pipeline-key { color: var(--muted); }
  .pipeline-val { color: var(--text); font-weight: 600; }
  .strike  { color: var(--green); }
  .nothing { color: var(--muted); }
  /* ── Live Data Feeds ── */
  .feed-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .75rem; margin-bottom: .75rem; }
  .feed-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: .75rem 1rem; }
  .feed-name { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: .25rem; }
  .feed-status { font-size: .95rem; font-weight: 700; }
  .feed-live  { color: var(--green); }
  .feed-error { color: var(--red); }
  .feed-meta  { font-size: .75rem; color: var(--muted); margin-top: .2rem; }
  .feed-error-msg { font-size: .72rem; color: var(--red); margin-top: .15rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
  #feeds-updated { font-size: .78rem; color: var(--muted); margin-bottom: .4rem; }
  .arb-row-pos { color: var(--green); }
  .arb-row-zero { color: var(--muted); }
  .tabs { display: flex; gap: .5rem; flex-wrap: wrap; margin: 1rem 0; border-bottom: 1px solid var(--border); }
  .tab-btn { background: transparent; color: var(--muted); border: 1px solid transparent; border-radius: 6px 6px 0 0; padding: .5rem .8rem; }
  .tab-btn.active { color: var(--text); background: var(--surface); border-color: var(--border); border-bottom-color: var(--surface); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .mono-small { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .74rem; color: var(--muted); }
  .toolbar-note { font-size: .8rem; color: var(--muted); }
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Outfit:wght@300;400;500;600;700&display=swap');
  :root {
    --bg:#0a0c10; --surface:#0f1218; --s1:#0f1218; --s2:#141820; --s3:#1a2030; --s4:#202840;
    --border:#ffffff0d; --border2:#ffffff18; --muted:#374060; --dim:#556080; --base:#8090b0;
    --text:#c0cedf; --hi:#eaf0ff; --teal:#00d4b8; --teal2:#00d4b820;
    --green:#00c87a; --green2:#00c87a18; --yellow:#f0a020; --amber:#f0a020;
    --amber2:#f0a02018; --red:#f03a58; --red2:#f03a5818; --blue:#4090ff; --blue2:#4090ff18;
    --purple:#4090ff; --mono:'IBM Plex Mono',monospace; --sans:'Outfit',sans-serif;
  }
  html, body { height: 100%; }
  body { background: var(--bg); color: var(--text); margin: 0; padding: 0; overflow: hidden; font-family: var(--sans); }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--muted); border-radius: 2px; }
  @keyframes fadein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .app-shell { display: flex; height: 100vh; width: 100vw; overflow: hidden; }
  .sidebar { width: 230px; flex-shrink: 0; background: var(--s1); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
  .brand { padding: 20px 20px 16px; border-bottom: 1px solid var(--border); }
  .brand-row { display: flex; align-items: center; gap: 10px; }
  .brand-mark { width: 32px; height: 32px; border-radius: 8px; background: linear-gradient(135deg,var(--teal),var(--blue)); display: flex; align-items: center; justify-content: center; color: var(--bg); font-weight: 800; }
  .brand-title { color: var(--hi); font-size: 13px; font-weight: 700; line-height: 1.1; }
  .brand-sub { color: var(--dim); font-family: var(--mono); font-size: 10px; margin-top: 3px; }
  .rail-status { display: flex; align-items: center; gap: 8px; padding: 11px 20px; border-bottom: 1px solid var(--border); font-family: var(--mono); font-size: 11px; color: var(--base); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); display: inline-block; flex-shrink: 0; }
  .dot.pulse { animation: pulse 2s ease-in-out infinite; }
  .nav { flex: 1; padding: 12px 10px; overflow-y: auto; }
  .tab-btn { width: 100%; display: flex; align-items: center; gap: 10px; padding: 10px 12px; margin-bottom: 3px; border-radius: 10px; border: 0; background: transparent; color: var(--base); cursor: pointer; font-family: var(--sans); font-size: 13px; text-align: left; }
  .tab-btn:hover { background: var(--s2); color: var(--hi); }
  .tab-btn.active { background: var(--s3); color: var(--hi); border: 0; }
  .tab-icon { color: var(--muted); font-size: 14px; width: 16px; }
  .tab-btn.active .tab-icon { color: var(--teal); }
  .rail-stats { padding: 14px 16px; border-top: 1px solid var(--border); display: grid; gap: 8px; }
  .rail-kv { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: var(--dim); }
  .rail-kv b { color: var(--teal); font-family: var(--mono); font-weight: 600; }
  .main { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
  .topbar { height: 66px; flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 32px; border-bottom: 1px solid var(--border); background: #0a0c10f2; }
  .top-badges { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .content { flex: 1; overflow: hidden; }
  .content > h1, .content > .tabs { display: none; }
  h1 { color: var(--hi); margin: 0; font-size: 20px; }
  h2 { color: var(--base); border-bottom: 1px solid var(--border); padding-bottom: .65rem; margin: 1.5rem 0 .95rem; font-size: .82rem; text-transform: uppercase; letter-spacing: .12em; }
  .page-sub { color: var(--base); font-size: 13px; margin-top: 4px; }
  .tab-panel { display: none; height: 100%; overflow-y: auto; padding: 28px 34px 40px; animation: fadein .2s ease; }
  .tab-panel.active { display: block; }
  table { font-size: .78rem; font-family: var(--mono); }
  th, td { padding: .62rem .65rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
  th { color: var(--dim); font-weight: 500; letter-spacing: .04em; background: var(--s1); position: sticky; top: 0; z-index: 1; }
  tbody tr:hover { background: #ffffff05; }
  .card, .stat-box, .feed-card { background: var(--s1); border: 1px solid var(--border); border-radius: 12px; }
  .card-title, .stat-label, .feed-name { font-family: var(--mono); color: var(--dim); font-size: .68rem; text-transform: uppercase; letter-spacing: .1em; }
  .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .stat-box { padding: 18px 20px; }
  .stat-value { font-size: 1.55rem; font-weight: 700; color: var(--hi); font-family: var(--mono); line-height: 1; }
  .stat-sub { color: var(--base); font-size: 11px; margin-top: 6px; font-family: var(--mono); }
  button { background: var(--teal2); color: var(--teal); border: 1px solid #00d4b840; border-radius: 9px; font-family: var(--sans); }
  button.secondary { background: var(--s2); color: var(--base); border: 1px solid var(--border2); }
  select, input[type=number], pre, code { background: var(--s2); color: var(--text); border: 1px solid var(--border2); font-family: var(--mono); }
  .badge { border-radius: 5px; font-family: var(--mono); letter-spacing: .06em; }
  .badge-ok { background: var(--green2); color: var(--green); } .badge-fail { background: var(--red2); color: var(--red); } .badge-rust { background: var(--blue2); color: var(--blue); }
  .feed-status, .card-value { font-family: var(--mono); }
  .feed-live, .profit-pos, .ok, .strike { color: var(--green); }
  .feed-error, .profit-neg, .err { color: var(--red); }
  .toolbar-note, .mono-small, #stream-status, #feeds-updated { font-family: var(--mono); color: var(--base); }
  .table-wrap { overflow-x: auto; background: var(--s1); border: 1px solid var(--border); border-radius: 14px; }
</style>
</head>
<body>
<div class="app-shell">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-row">
        <div class="brand-mark">A</div>
        <div>
          <div class="brand-title">Apex-Omega</div>
          <div class="brand-sub">SSOT · Polygon</div>
        </div>
      </div>
    </div>
    <div class="rail-status">
      <span class="dot pulse" style="background: {{ 'var(--green)' if chain_ok else 'var(--red)' }}"></span>
      <span>{{ 'LIVE' if chain_ok else 'RPC DOWN' }}</span>
      <span style="margin-left:auto;color:var(--dim)">5000</span>
    </div>
    <nav class="nav">
      <button class="tab-btn active" data-tab="overview"><span class="tab-icon">O</span><span>Overview</span></button>
      <button class="tab-btn" data-tab="routes"><span class="tab-icon">R</span><span>Routes</span></button>
      <button class="tab-btn" data-tab="dna"><span class="tab-icon">D</span><span>Execution DNA</span></button>
      <button class="tab-btn" data-tab="prices"><span class="tab-icon">P</span><span>Venue Prices</span></button>
    </nav>
    <div class="rail-stats">
      <div class="rail-kv"><span>Routes</span><b id="rail-route-count">--</b></div>
      <div class="rail-kv"><span>Best Net</span><b id="rail-best-net">--</b></div>
      <div class="rail-kv"><span>Price Rows</span><b id="rail-price-count">--</b></div>
    </div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div>
        <h1>Apex-Omega-v6 Dashboard</h1>
        <div class="page-sub">Real-time arbitrage control plane · all displayed values trace to backend artifacts</div>
      </div>
      <div class="top-badges">
        <span class="badge {{ 'badge-rust' if rust_ok else 'badge-fail' }}" title="Rust math core">Rust {{ 'OK' if rust_ok else 'FAIL' }}</span>
        <span class="badge {{ 'badge-ok' if chain_ok else 'badge-fail' }}" title="Polygon RPC">Chain {{ 'OK' if chain_ok else 'FAIL' }}</span>
        <span class="badge {{ 'badge-ok' if modules_ok else 'badge-fail' }}">Modules {{ modules_loaded }}/{{ modules_total }}</span>
      </div>
    </header>
    <div class="content">
<h1>
  ⚡ Apex-Omega-v6
  <span class="badge {{ 'badge-rust' if rust_ok else 'badge-fail' }}" title="Rust math core">
    Rust {{ '✓' if rust_ok else '✗' }}
  </span>
  <span class="badge {{ 'badge-ok' if chain_ok else 'badge-fail' }}" title="Polygon RPC">
    Chain {{ '✓' if chain_ok else '✗' }}
  </span>
  <span class="badge {{ 'badge-ok' if modules_ok else 'badge-fail' }}">
    Modules {{ modules_loaded }}/{{ modules_total }}
  </span>
</h1>

<div class="tabs">
  <button class="tab-btn active" data-tab="overview">Overview</button>
  <button class="tab-btn" data-tab="routes">Routes</button>
  <button class="tab-btn" data-tab="dna">Execution DNA</button>
  <button class="tab-btn" data-tab="prices">Venue Prices</button>
</div>

<section class="tab-panel active" id="tab-overview">
<div class="metric-grid">
  <div class="stat-box">
    <div class="stat-label">ROUTE RECORDS</div>
    <div class="stat-value" id="stat-route-count">--</div>
    <div class="stat-sub">latest dry-run artifact</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">BEST NET EDGE</div>
    <div class="stat-value" id="stat-best-net">--</div>
    <div class="stat-sub">after slippage and flash fee; gas ranks submission</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">BEST SPREAD</div>
    <div class="stat-value" id="stat-best-spread">--</div>
    <div class="stat-sub">executable spread after math</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">PRICE COLUMNS</div>
    <div class="stat-value" id="stat-price-ready">--</div>
    <div class="stat-sub">buy/sell USDC in route feed</div>
  </div>
</div>

<h2>Live Capability Surface</h2>
<div class="grid">
  <div class="card"><div class="card-title">Live Data Feeds</div>
    <div class="card-value">POLL</div><div class="stat-sub">GET /api/feeds · RPC, gas, market feed health</div></div>
  <div class="card"><div class="card-title">Scanner Stream</div>
    <div class="card-value">SSE</div><div class="stat-sub">GET /api/scan/stream · live Polygon opportunities</div></div>
  <div class="card"><div class="card-title">SSOT Math</div>
    <div class="card-value">C1/C2</div><div class="stat-sub">GET /api/pipeline · deterministic route math</div></div>
  <div class="card"><div class="card-title">Route Transparency</div>
    <div class="card-value" id="cap-route-count">--</div><div class="stat-sub">GET /api/routes · raw and after-math spreads</div></div>
  <div class="card"><div class="card-title">Venue Prices</div>
    <div class="card-value" id="cap-price-count">--</div><div class="stat-sub">GET /api/token-prices · executable pool prices</div></div>
  <div class="card"><div class="card-title">Module Health</div>
    <div class="card-value {{ 'ok' if modules_ok else 'err' }}">{{ modules_loaded }}/{{ modules_total }}</div>
    <div class="stat-sub">GET /api/modules · import readiness</div></div>
</div>

<h2>Live Data Feeds</h2>
<div class="controls">
  <button id="btn-feeds-poll">⟳ Poll now</button>
  <button id="btn-feeds-stream" class="secondary">▶ Auto-poll (5s)</button>
  <button id="btn-feeds-stop" class="secondary" disabled>■ Stop</button>
  <span id="feeds-poll-status" style="font-size:.82rem;color:var(--muted)"></span>
</div>
<div id="feeds-updated"></div>
<div class="feed-grid" id="feed-cards">
  <!-- populated by JS -->
</div>

<div id="feeds-arb-section" style="display:none">
  <h3 style="font-size:.85rem;color:var(--muted);margin:.75rem 0 .35rem">CPMM Arbitrage Signals (The Graph + CoinGecko)</h3>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>#</th><th>Pair</th><th>Spread (bps)</th>
        <th>Buy Pool Fee</th><th>Sell Pool Fee</th>
        <th>TVL Buy $</th><th>TVL Sell $</th>
        <th>CPMM Profit $</th>
      </tr>
    </thead>
    <tbody id="arb-tbody"><tr><td colspan="8" style="color:var(--muted);text-align:center">No signals yet.</td></tr></tbody>
  </table>
  </div>
</div>

<h2>Live Scan — Streaming Feed</h2>
<div class="controls">
  <label>Provider
    <select id="provider">
      <option value="balancer">Balancer (0 bps)</option>
      <option value="aave_v3">Aave V3 (9 bps)</option>
      <option value="uniswap_v3">UniV3 (0 bps)</option>
      <option value="none">None / own capital</option>
    </select>
  </label>
  <label>Max scans <input type="number" id="max-scans" value="3" min="1" max="20" style="width:60px"></label>
  <label>Min profit $ <input type="number" id="min-profit" value="0.01" min="0" step="0.01" style="width:72px"></label>
  <label>Size $ <input type="number" id="trade-size" value="10000" min="100" step="1000" style="width:90px"></label>
  <button id="btn-stream">▶ Start stream</button>
  <button id="btn-stop" class="secondary" disabled>■ Stop</button>
  <span id="stream-status"></span>
</div>

<div style="overflow-x:auto">
<table>
  <thead>
    <tr>
      <th>#</th><th>Pair</th><th>Buy DEX</th><th>Sell DEX</th>
      <th>Spread (bps)</th><th>Size $</th><th>Gross $</th>
      <th>Net Edge $</th><th>P(fill)</th><th>E[profit] $</th><th>Mode</th>
    </tr>
  </thead>
  <tbody id="opp-tbody"><tr><td colspan="11" style="color:var(--muted);text-align:center">
    Press ▶ Start stream to begin scanning.
  </td></tr></tbody>
</table>
</div>

<h2>SSOT Pipeline — C1/C2 Math Layer</h2>
<div class="controls">
  <label>r1_in <input type="number" id="r1in" value="1000000" style="width:110px"></label>
  <label>r1_out <input type="number" id="r1out" value="2520000" style="width:110px"></label>
  <label>fee1 <input type="number" id="fee1" value="0.003" step="0.0001" style="width:80px"></label>
  <label>r2_in <input type="number" id="r2in" value="2590000" style="width:110px"></label>
  <label>r2_out <input type="number" id="r2out" value="1000000" style="width:110px"></label>
  <label>fee2 <input type="number" id="fee2" value="0.003" step="0.0001" style="width:80px"></label>
  <label>c_total_exec <input type="number" id="ctotal" value="0.5" step="0.1" style="width:70px"></label>
  <button id="btn-pipeline">Run Pipeline</button>
</div>
<div id="pipeline-out"></div>

<h2>Core Modules</h2>
<table>
  <thead><tr><th>Module</th><th>Status</th><th>Error</th></tr></thead>
  <tbody>
  {% for row in modules %}
    <tr>
      <td><code>{{ row.module }}</code></td>
      <td class="{{ 'ok' if row.ok else 'err' }}">{{ 'OK' if row.ok else 'FAIL' }}</td>
      <td style="font-size:.8rem;color:var(--muted)">{{ row.error or '' }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<h2>API Reference</h2>
<div class="grid">
  <div class="card"><div class="card-title">Health</div>
    <code>GET /healthz</code></div>
  <div class="card"><div class="card-title">System Status</div>
    <code>GET /api/status</code></div>
  <div class="card"><div class="card-title">Module Status</div>
    <code>GET /api/modules</code></div>
  <div class="card"><div class="card-title">Batch Scan</div>
    <code>GET /api/scan?n=20&amp;provider=balancer</code></div>
  <div class="card"><div class="card-title">Streaming Scan (SSE)</div>
    <code>GET /api/scan/stream</code></div>
  <div class="card"><div class="card-title">SSOT Pipeline</div>
    <code>GET /api/pipeline?r1_in=…</code></div>
  <div class="card"><div class="card-title">Live Data Feeds</div>
    <code>GET /api/feeds</code></div>
  <div class="card"><div class="card-title">Last Dry-Run Results</div>
    <code>GET /api/results</code></div>
</div>
</section>

<section class="tab-panel" id="tab-routes">
  <h2>Route Transparency</h2>
  <div class="controls">
    <button id="btn-routes-refresh">Refresh routes</button>
    <span id="routes-status" class="toolbar-note"></span>
  </div>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>#</th><th>Pair</th><th>Buy Venue</th><th>Sell Venue</th>
        <th>Buy Price USDC</th><th>Sell Price USDC</th>
        <th>Raw Spread</th><th>After Math</th><th>Math Cost</th>
        <th>Size $</th><th>Flash Fee</th><th>Net $</th><th>Buy Pool</th><th>Sell Pool</th>
      </tr>
    </thead>
    <tbody id="routes-tbody">
      <tr><td colspan="14" style="color:var(--muted);text-align:center">Load route data from the latest dry-run CSV.</td></tr>
    </tbody>
  </table>
  </div>
</section>

<section class="tab-panel" id="tab-dna">
  <h2>Execution DNA Dry Run</h2>
  <div class="controls">
    <button id="btn-dna-refresh">Refresh DNA cards</button>
    <span id="dna-status" class="toolbar-note"></span>
  </div>
  <div id="dna-blockers" class="mono-small"></div>
  <div id="dna-cards" class="grid">
    <div class="card"><div class="stat-sub">Load no-broadcast C1/C2 dry-run payload cards.</div></div>
  </div>
</section>

<section class="tab-panel" id="tab-prices">
  <h2>Venue Token Prices</h2>
  <div class="controls">
    <label>Quote size $
      <input type="number" id="price-size" value="10000" min="100" step="1000" style="width:90px">
    </label>
    <label>Sort
      <select id="price-sort">
        <option value="lowest">Lowest executable</option>
        <option value="highest">Highest executable</option>
        <option value="venue">Venue</option>
        <option value="token_low">Token then lowest</option>
      </select>
    </label>
    <button id="btn-prices-refresh">Refresh prices</button>
    <span id="prices-status" class="toolbar-note"></span>
  </div>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>#</th><th>Token</th><th>Quote</th><th>Venue</th><th>Pair</th>
        <th>Lowest Executable USDC</th><th>Highest Executable USDC</th>
        <th>Direct Contract USDC</th><th>Reserve In</th><th>Reserve Out</th><th>Pool</th>
      </tr>
    </thead>
    <tbody id="prices-tbody">
      <tr><td colspan="11" style="color:var(--muted);text-align:center">Fetch live pool prices to populate this table.</td></tr>
    </tbody>
  </table>
  </div>
</section>

<script>
// Tabs and read-only transparency surfaces
function setActiveTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${tabName}`);
  });
  if (tabName === 'routes') loadRoutes();
  if (tabName === 'dna') loadExecutionDna();
  if (tabName === 'prices') loadTokenPrices();
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
});

function fmtBps(v, dec=2) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(dec) + ' bps';
}

function fmtMoney(v, dec=4) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  return '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits: dec, maximumFractionDigits: dec});
}

function fmtPriceCell(v, source) {
  if (v == null || v === '' || Number.isNaN(Number(v))) {
    return source === 'legacy_csv_missing_prices' ? '<span class="warn">rerun dry-run</span>' : '-';
  }
  return fmtMoney(v, 8);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function shortAddr(v) {
  if (!v) return '-';
  const s = String(v);
  return s.length > 14 ? `${s.slice(0, 8)}...${s.slice(-6)}` : s;
}

async function loadRoutes() {
  const status = document.getElementById('routes-status');
  const tbody = document.getElementById('routes-tbody');
  status.textContent = 'Loading latest dry-run routes...';
  try {
    const resp = await fetch('/api/routes');
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    const rows = data.records || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="14" style="color:var(--muted);text-align:center">No dry-run route records found.</td></tr>';
      status.textContent = 'No routes available.';
      return;
    }
    tbody.innerHTML = '';
    rows.slice(0, 100).forEach((r, idx) => {
      const netCls = Number(r.expected_net_edge || 0) >= 0 ? 'profit-pos' : 'profit-neg';
      const spot = Number(r.raw_spread_before_math_bps ?? r.spot_spread_bps ?? r.raw_spread_bps ?? 0);
      const after = Number(r.spread_after_math_bps ?? r.executable_spread_bps ?? r.raw_spread_bps ?? 0);
      const delta = Number(r.spread_math_delta_bps ?? (spot - after));
      tbody.innerHTML += `<tr>
        <td>${idx + 1}</td>
        <td><b>${r.pair || '-'}</b></td>
        <td>${r.buy_dex || '-'}</td>
        <td>${r.sell_dex || '-'}</td>
        <td>${fmtPriceCell(r.buy_price_usdc, r.price_source)}</td>
        <td>${fmtPriceCell(r.sell_price_usdc, r.price_source)}</td>
        <td>${spot.toFixed(2)} bps</td>
        <td>${after.toFixed(2)} bps</td>
        <td>${delta.toFixed(2)} bps</td>
        <td>${fmtMoney(r.trade_size_usd, 0)}</td>
        <td>${fmtMoney(r.flash_fee_usd, 4)}</td>
        <td class="${netCls}">${fmtMoney(r.expected_net_edge, 4)}</td>
        <td class="mono-small" title="${r.buy_pool || ''}">${shortAddr(r.buy_pool)}</td>
        <td class="mono-small" title="${r.sell_pool || ''}">${shortAddr(r.sell_pool)}</td>
      </tr>`;
    });
    const missing = Number(data.missing_price_count || 0);
    const bestNet = Math.max(...rows.map(r => Number(r.expected_net_edge || 0)));
    const bestSpread = Math.max(...rows.map(r => Number(r.spread_after_math_bps ?? r.executable_spread_bps ?? r.raw_spread_bps ?? 0)));
    setText('stat-route-count', rows.length.toLocaleString());
    setText('rail-route-count', rows.length.toLocaleString());
    setText('cap-route-count', rows.length.toLocaleString());
    setText('stat-best-net', fmtMoney(bestNet, 4));
    setText('rail-best-net', fmtMoney(bestNet, 2));
    setText('stat-best-spread', `${bestSpread.toFixed(2)} bps`);
    setText('stat-price-ready', missing ? `${rows.length - missing}/${rows.length}` : 'READY');
    status.textContent = missing
      ? `${rows.length} routes loaded; ${missing} legacy rows need a fresh dry-run for buy/sell prices.`
      : `${rows.length} routes loaded from ${data.file || 'dry-run results'}.`;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="14" class="err" style="text-align:center">${e.message}</td></tr>`;
    status.textContent = 'Route load failed.';
  }
}

async function loadExecutionDna() {
  const status = document.getElementById('dna-status');
  const cards = document.getElementById('dna-cards');
  const blockers = document.getElementById('dna-blockers');
  status.textContent = 'Building no-broadcast C1/C2 payload DNA...';
  try {
    const resp = await fetch('/api/execution-dna?limit=20');
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    const rows = data.cards || [];
    blockers.textContent = data.live_blockers && data.live_blockers.length
      ? 'Live blockers: ' + data.live_blockers.join(' | ')
      : 'Live blockers: none reported by config gate; still require fresh fork sim and C2 Merkle proof validation before broadcast.';
    if (!rows.length) {
      cards.innerHTML = '<div class="card"><div class="stat-sub">No executable dry-run payload cards built.</div></div>';
      status.textContent = 'No DNA cards available.';
      return;
    }
    cards.innerHTML = '';
    rows.forEach((r) => {
      const m = r.math || {};
      const c1 = (r.cycle || {}).c1 || {};
      const c2 = (r.cycle || {}).c2 || {};
      const p1 = (r.payloads || {}).c1 || {};
      const p2 = (r.payloads || {}).c2 || {};
      cards.innerHTML += `
        <div class="card">
          <div class="card-title">${r.card_id} · ${r.pair}</div>
          <div class="stat-sub">${r.mode} · ${r.source}</div>
          <div class="mono-small">Cycle: ${r.cycle_id}</div>
          <div class="mono-small">C1: <span class="${c1.decision === 'STRIKE' ? 'strike' : 'err'}">${c1.decision}</span> → ${shortAddr(c1.target)}</div>
          <div class="mono-small">C2: <span class="${c2.decision && c2.decision.includes('STRIKE') ? 'strike' : 'err'}">${c2.decision}</span> → ${shortAddr(c2.target)}</div>
          <hr style="border-color:var(--border);border-style:solid none none;margin:10px 0">
          <div class="mono-small">Amount: ${fmtMoney(m.amount_in, 4)}</div>
          <div class="mono-small">Gross: ${fmtMoney(m.p_gross, 4)} (${fmtBps(m.gross_bps)})</div>
          <div class="mono-small">Flash fee: ${fmtMoney(m.flash_fee, 4)} (${fmtBps(m.flash_fee_bps)})</div>
          <div class="mono-small">Route net: ${fmtMoney(m.p_net_route_token, 4)} (${fmtBps(m.route_net_bps)})</div>
          <div class="mono-small">C1 owner edge: ${fmtMoney(m.c1_owner_submission_edge, 4)} (${fmtBps(m.c1_owner_edge_bps)})</div>
          <div class="mono-small">C1 payload: ${p1.payload_bytes} bytes · ${shortAddr(p1.payload_keccak)}</div>
          <div class="mono-small">C2 leaf: ${shortAddr(p2.merkle_leaf)} · proof required</div>
          <details><summary class="mono-small">Full variables</summary><pre>${JSON.stringify(r, null, 2)}</pre></details>
        </div>`;
    });
    status.textContent = `${rows.length} no-broadcast executable DNA cards built.`;
  } catch (e) {
    cards.innerHTML = `<div class="card"><div class="err">${e.message}</div></div>`;
    status.textContent = 'DNA load failed.';
  }
}

async function loadTokenPrices() {
  const status = document.getElementById('prices-status');
  const tbody = document.getElementById('prices-tbody');
  const size = document.getElementById('price-size').value || '10000';
  const sort = document.getElementById('price-sort').value || 'lowest';
  status.textContent = 'Fetching live pools and executable prices...';
  tbody.innerHTML = '<tr><td colspan="11" style="color:var(--muted);text-align:center">Live pool discovery in progress...</td></tr>';
  try {
    const resp = await fetch(`/api/token-prices?size=${encodeURIComponent(size)}&sort=${encodeURIComponent(sort)}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    const rows = data.records || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="11" style="color:var(--muted);text-align:center">No executable token price rows found.</td></tr>';
      status.textContent = 'No rows available.';
      return;
    }
    tbody.innerHTML = '';
    rows.slice(0, 200).forEach((r, idx) => {
      tbody.innerHTML += `<tr>
        <td>${idx + 1}</td>
        <td><b>${r.token || '-'}</b></td>
        <td>${r.quote_token || '-'}</td>
        <td>${r.venue || '-'}</td>
        <td>${r.pair || '-'}</td>
        <td>${fmtMoney(r.lowest_executable_usdc, 8)}</td>
        <td>${fmtMoney(r.highest_executable_usdc, 8)}</td>
        <td>${fmtMoney(r.direct_contract_price_usdc, 8)}</td>
        <td>${Number(r.reserve_in || 0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
        <td>${Number(r.reserve_out || 0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
        <td class="mono-small" title="${r.pool || ''}">${shortAddr(r.pool)}</td>
      </tr>`;
    });
    setText('rail-price-count', rows.length.toLocaleString());
    setText('cap-price-count', rows.length.toLocaleString());
    status.textContent = `${rows.length} executable price rows loaded at quote size $${Number(data.quote_size_usd).toLocaleString()}.`;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11" class="err" style="text-align:center">${e.message}</td></tr>`;
    status.textContent = 'Price load failed.';
  }
}

document.getElementById('btn-routes-refresh').onclick = loadRoutes;
document.getElementById('btn-dna-refresh').onclick = loadExecutionDna;
document.getElementById('btn-prices-refresh').onclick = loadTokenPrices;
loadRoutes();

// ── Live Data Feeds ───────────────────────────────────────────────────────────
const FEED_LABELS = {
  the_graph:    'The Graph (Uniswap V3)',
  coingecko:    'CoinGecko Prices',
  etherscan_gas:'PolygonScan Gas Oracle',
  polygon_rpc:  'Polygon RPC',
};

let feedsEvt = null;
let feedsPollTimer = null;

function renderFeeds(data) {
  const cards = document.getElementById('feed-cards');
  const upd = document.getElementById('feeds-updated');
  const ts = new Date(data.timestamp * 1000).toLocaleTimeString();
  upd.textContent = `Last polled: ${ts}  |  All live: ${data.all_live ? '✓' : '✗'}`;

  cards.innerHTML = '';
  for (const [key, state] of Object.entries(data.feeds || {})) {
    const isLive = state.status === 'LIVE';
    const label = FEED_LABELS[key] || key;
    let meta = '';
    if (key === 'polygon_rpc' && data.block_number) {
      meta = `Block ${data.block_number.toLocaleString()}  •  Gas ${(data.rpc_gas_price_gwei||0).toFixed(1)} gwei`;
    } else if (key === 'etherscan_gas' && data.gas_base_fee_gwei != null) {
      meta = `Base ${(data.gas_base_fee_gwei||0).toFixed(2)} • Safe ${(data.gas_safe_gwei||0).toFixed(1)} • Fast ${(data.gas_fast_gwei||0).toFixed(1)} gwei`;
    } else if (key === 'the_graph') {
      meta = `${(data.pools||[]).length} pools`;
    } else if (key === 'coingecko') {
      const prices = data.token_prices_usd || {};
      const pol = (prices['WMATIC'] || prices['POL'] || prices['MATIC'] || 0);
      meta = pol ? `POL $${pol.toFixed(3)}` : '';
    }
    const errLine = (!isLive && state.error)
      ? `<div class="feed-error-msg" title="${state.error}">${state.error}</div>` : '';
    const latency = isLive ? `${state.latency_ms.toFixed(0)} ms` : '';
    cards.innerHTML += `
      <div class="feed-card">
        <div class="feed-name">${label}</div>
        <div class="feed-status ${isLive ? 'feed-live' : 'feed-error'}">${state.status}</div>
        ${meta ? `<div class="feed-meta">${meta}</div>` : ''}
        ${latency ? `<div class="feed-meta">${latency}</div>` : ''}
        ${errLine}
      </div>`;
  }

  // Arb signals table
  const signals = data.arb_signals || [];
  const arbSection = document.getElementById('feeds-arb-section');
  const arbTbody = document.getElementById('arb-tbody');
  if (signals.length) {
    arbSection.style.display = '';
    arbTbody.innerHTML = '';
    signals.slice(0, 20).forEach((s, idx) => {
      const profCls = s.cpmm_arb_profit_usd > 0 ? 'arb-row-pos' : 'arb-row-zero';
      arbTbody.innerHTML += `<tr>
        <td>${idx+1}</td>
        <td><b>${s.pair}</b></td>
        <td>${s.spread_bps.toFixed(1)}</td>
        <td>${(s.fee_buy*100).toFixed(3)}%</td>
        <td>${(s.fee_sell*100).toFixed(3)}%</td>
        <td>$${Number(s.tvl_buy_usd).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
        <td>$${Number(s.tvl_sell_usd).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
        <td class="${profCls}">$${s.cpmm_arb_profit_usd.toFixed(4)}</td>
      </tr>`;
    });
  } else {
    arbSection.style.display = 'none';
  }
}

async function pollFeeds() {
  const statusEl = document.getElementById('feeds-poll-status');
  statusEl.textContent = 'Polling…';
  try {
    const resp = await fetch('/api/feeds');
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    renderFeeds(data);
    statusEl.textContent = data.all_live ? '✓ All feeds LIVE' : '⚠ Feed error — check cards';
  } catch (e) {
    statusEl.textContent = '✗ ' + e.message;
  }
}

document.getElementById('btn-feeds-poll').onclick = pollFeeds;

document.getElementById('btn-feeds-stream').onclick = () => {
  if (feedsPollTimer) return;
  const btn = document.getElementById('btn-feeds-stream');
  const stopBtn = document.getElementById('btn-feeds-stop');
  btn.disabled = true; stopBtn.disabled = false;
  pollFeeds();
  feedsPollTimer = setInterval(pollFeeds, 5000);
};

document.getElementById('btn-feeds-stop').onclick = () => {
  if (feedsPollTimer) { clearInterval(feedsPollTimer); feedsPollTimer = null; }
  const btn = document.getElementById('btn-feeds-stream');
  const stopBtn = document.getElementById('btn-feeds-stop');
  btn.disabled = false; stopBtn.disabled = true;
  document.getElementById('feeds-poll-status').textContent = 'Stopped.';
};

// Auto-start feed polling on page load
pollFeeds();
</script>

<script>
// ── Streaming scan ────────────────────────────────────────────────────────────
let evtSrc = null;
let rowCount = 0;
const tbody = document.getElementById('opp-tbody');
const streamStatus = document.getElementById('stream-status');
const btnStream = document.getElementById('btn-stream');
const btnStop   = document.getElementById('btn-stop');

function fmtNum(v, dec=2) {
  if (v == null) return '—';
  return Number(v).toFixed(dec);
}

function appendRow(rec) {
  rowCount++;
  if (rowCount === 1) tbody.innerHTML = '';
  const netCls = rec.expected_net_edge >= 0 ? 'profit-pos' : 'profit-neg';
  const mode = rec.sell_dex === 'triangular' ? '△ TRI' : '↔ ARB';
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${rowCount}</td>
    <td><b>${rec.pair}</b></td>
    <td>${rec.buy_dex}</td>
    <td>${rec.sell_dex}</td>
    <td>${fmtNum(rec.raw_spread_bps,1)}</td>
    <td>$${fmtNum(rec.trade_size_usd,0)}</td>
    <td>$${fmtNum(rec.gross_profit_usd,4)}</td>
    <td class="${netCls}">$${fmtNum(rec.expected_net_edge,4)}</td>
    <td>${fmtNum(rec.p_fill,3)}</td>
    <td class="profit-pos">$${fmtNum(rec.e_profit,4)}</td>
    <td>${mode}</td>`;
  tbody.prepend(tr);
}

btnStream.onclick = () => {
  if (evtSrc) { evtSrc.close(); evtSrc = null; }
  rowCount = 0;
  tbody.innerHTML = '<tr><td colspan="11" style="color:var(--muted);text-align:center">Connecting…</td></tr>';
  const provider  = document.getElementById('provider').value;
  const maxScans  = document.getElementById('max-scans').value;
  const minProfit = document.getElementById('min-profit').value;
  const size      = document.getElementById('trade-size').value;
  const url = `/api/scan/stream?provider=${provider}&max_scans=${maxScans}&min_profit=${minProfit}&size=${size}`;
  evtSrc = new EventSource(url);
  btnStream.disabled = true; btnStop.disabled = false;
  const spinner = '<span class="spinner"></span>';
  streamStatus.innerHTML = spinner + ' Scanning…';

  evtSrc.addEventListener('opportunity', e => {
    const rec = JSON.parse(e.data);
    appendRow(rec);
    streamStatus.innerHTML = spinner + ` ${rowCount} records`;
  });
  evtSrc.addEventListener('summary', e => {
    const s = JSON.parse(e.data);
    streamStatus.textContent = `✓ Done: ${s.profitable_count}/${s.total} profitable, E[profit] sum $${s.sum_e_profit.toFixed(4)}`;
    evtSrc.close(); evtSrc = null;
    btnStream.disabled = false; btnStop.disabled = true;
  });
  evtSrc.addEventListener('error_event', e => {
    streamStatus.textContent = '✗ Stream error: ' + (JSON.parse(e.data).message || 'unknown');
    evtSrc.close(); evtSrc = null;
    btnStream.disabled = false; btnStop.disabled = true;
  });
  evtSrc.onerror = () => {
    if (evtSrc && evtSrc.readyState === EventSource.CLOSED) {
      streamStatus.textContent = 'Stream closed.';
      btnStream.disabled = false; btnStop.disabled = true;
    }
  };
};

btnStop.onclick = () => {
  if (evtSrc) { evtSrc.close(); evtSrc = null; }
  streamStatus.textContent = 'Stopped.';
  btnStream.disabled = false; btnStop.disabled = true;
};

// ── SSOT Pipeline ─────────────────────────────────────────────────────────────
document.getElementById('btn-pipeline').onclick = async () => {
  const btn = document.getElementById('btn-pipeline');
  btn.disabled = true; btn.textContent = 'Running…';
  const r1_in  = document.getElementById('r1in').value;
  const r1_out = document.getElementById('r1out').value;
  const fee1   = document.getElementById('fee1').value;
  const r2_in  = document.getElementById('r2in').value;
  const r2_out = document.getElementById('r2out').value;
  const fee2   = document.getElementById('fee2').value;
  const c_total_exec= document.getElementById('ctotal').value;
  const url = `/api/pipeline?r1_in=${r1_in}&r1_out=${r1_out}&fee1=${fee1}&r2_in=${r2_in}&r2_out=${r2_out}&fee2=${fee2}&c_total_exec=${c_total_exec}`;
  try {
    const resp = await fetch(url);
    const j = await resp.json();
    if (!resp.ok) { throw new Error(j.error || resp.statusText); }
    const decCls = j.c2_decision === 'STRIKE' ? 'strike' : 'nothing';
    const auditOk = j.audit?.passed;
    document.getElementById('pipeline-out').innerHTML = `
      <div class="pipeline-row">
        <div class="pipeline-kv"><span class="pipeline-key">Decision </span>
          <span class="pipeline-val ${decCls}">${j.c2_decision}</span></div>
        <div class="pipeline-kv"><span class="pipeline-key">Best Size </span>
          <span class="pipeline-val">${Number(j.best_size).toFixed(4)} A</span></div>
        <div class="pipeline-kv"><span class="pipeline-key">Net Profit </span>
          <span class="pipeline-val">$${Number(j.p_net_deterministic).toFixed(6)}</span></div>
        <div class="pipeline-kv"><span class="pipeline-key">EV </span>
          <span class="pipeline-val">$${Number(j.ev).toFixed(6)}</span></div>
        <div class="pipeline-kv"><span class="pipeline-key">Audit </span>
          <span class="pipeline-val ${auditOk ? 'ok' : 'err'}">${auditOk ? 'PASS' : 'FAIL'}</span></div>
        <div class="pipeline-kv"><span class="pipeline-key">Batch hits </span>
          <span class="pipeline-val">${j.batch_summary?.n_profitable_strikes}/${j.batch_summary?.n_runs}</span></div>
      </div>
      ${j.audit?.violations?.length ? '<pre style="margin-top:.5rem;color:var(--red)">' + j.audit.violations.join('\n') + '</pre>' : ''}
    `;
  } catch (e) {
    document.getElementById('pipeline-out').innerHTML =
      `<pre class="err">Error: ${e.message}</pre>`;
  } finally {
    btn.disabled = false; btn.textContent = 'Run Pipeline';
  }
};
</script>
    </div>
  </main>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    mods = _module_status()
    rust = _rust_status()
    rpc = os.getenv("POLYGON_RPC", _DEFAULT_RPC)
    chain = _chain_status(rpc)
    return render_template_string(
        _DASHBOARD_HTML,
        modules=mods,
        rust_ok=rust["available"],
        chain_ok=chain["connected"],
        modules_ok=all(m["ok"] for m in mods),
        modules_loaded=sum(1 for m in mods if m["ok"]),
        modules_total=len(mods),
    )


@app.route("/healthz")
def healthz():
    mods = _module_status()
    rust = _rust_status()
    readiness = _readiness_status()
    ok = all(m["ok"] for m in mods) and rust["available"] and bool(readiness.get("production_ready"))
    return jsonify({
        "ok": ok,
        "rust_core": rust["available"],
        "modules_loaded": sum(1 for m in mods if m["ok"]),
        "modules_total": len(mods),
        "production_ready": readiness.get("production_ready", False),
        "missing_live_env": readiness.get("missing_live_env", []),
    })


@app.route("/api/modules")
def api_modules():
    return jsonify(_module_status())


@app.route("/api/status")
def api_status():
    """Full system status: Rust core, chain connectivity, modules, env config."""
    rust = _rust_status()
    rpc = request.args.get("rpc") or os.getenv("POLYGON_RPC", _DEFAULT_RPC)
    chain = _chain_status(rpc)
    mods = _module_status()
    readiness = _readiness_status()
    return jsonify({
        "rust_core": rust,
        "chain": chain,
        "modules": mods,
        "readiness": readiness,
        "env": {
            "APEX_POL_USD": os.getenv("APEX_POL_USD", "not set"),
            "APEX_ETH_USD": os.getenv("APEX_ETH_USD", "not set"),
            "APEX_SEND_TX": os.getenv("APEX_SEND_TX", "0"),
            "FLASH_LOAN_PROVIDER": os.getenv("FLASH_LOAN_PROVIDER", "balancer"),
            "execution_enabled": os.getenv("APEX_SEND_TX", "0") not in ("0", "", "false", "False"),
        },
        "timestamp": time.time(),
    })


@app.route("/api/scan")
def api_scan():
    """Run a Polygon scan (blocking) and return all records as JSON.

    Query params:
        n        target opportunity count (1-100, default 20)
        size     max trade size USD (default 10_000)
        provider flash-loan provider name (default balancer)
        rpc      override Polygon RPC URL
        max_scans  max scan rounds (default 5)
        min_profit min net profit filter in USD (default 1.0)
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
    rpc = request.args.get("rpc") or os.getenv("POLYGON_RPC", _DEFAULT_RPC)

    from dry_run import run_live_opportunity_scan  # noqa: PLC0415

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


@app.route("/api/scan/stream")
def api_scan_stream():
    """Server-Sent Events endpoint — streams each OpportunityRecord as it is found.

    Events:
        opportunity   — one JSON OpportunityRecord per profitable find
        summary       — final aggregate stats when the scan completes
        error_event   — if an unrecoverable error occurs
    """
    try:
        max_scans = int(request.args.get("max_scans", "3"))
    except ValueError:
        max_scans = 3
    try:
        size = float(request.args.get("size", "10000"))
    except ValueError:
        size = 10_000.0
    try:
        min_profit = float(request.args.get("min_profit", "0.01"))
    except ValueError:
        min_profit = 0.01
    provider = request.args.get("provider", "balancer")
    rpc = request.args.get("rpc") or os.getenv("POLYGON_RPC", _DEFAULT_RPC)

    def generate():
        import importlib.util  # noqa: PLC0415

        # Keep a heartbeat comment going so the connection stays alive
        yield ": apex-omega-v6 scan stream\n\n"

        try:
            from dry_run import (  # noqa: PLC0415
                _discover_pools,
                _filter_pool_universe,
                _derive_token_prices_usd,
                _compute_opportunity,
                _scan_triangular_cycles,
                _resolve_flash_loan_fee_rate,
                _env_float,
                _env_float_list,
                _GAS_UNITS,
                _PAIRS,
            )
            from web3 import Web3  # noqa: PLC0415
            from apex_omega_core.core.slippage_sentinel import SlippageSentinel  # noqa: PLC0415
            from apex_omega_core.core.mev_gas_oracle import GasOracle, TipOptimizer  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            yield _sse_event({"message": _safe_error(exc)}, event="error_event")
            return

        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))

        # Hard-fail on RPC miss — simulation is not allowed.
        _connected = False
        for _attempt in range(1, 4):
            if w3.is_connected():
                _connected = True
                break
            import time as _t  # noqa: PLC0415
            _t.sleep(2)
        if not _connected:
            yield _sse_event(
                {
                    "message": (
                        "Cannot reach Polygon RPC after 3 attempts. "
                        "Set POLYGON_RPC in your environment and restart."
                    )
                },
                event="error_event",
            )
            return

        sentinel = SlippageSentinel()
        gas_oracle = GasOracle(rpc_url=rpc, w3=w3)
        flash_fee_rate = _resolve_flash_loan_fee_rate(provider)
        min_flash_loan_usd = _env_float("MIN_FLASH_LOAN_USD", 50.0)
        max_flash_loan_usd = _env_float("MAX_FLASH_LOAN_USD", 1_000_000.0)
        max_flash_tvl_fraction = _env_float("MAX_FLASH_TVL_FRACTION", 0.15)
        flash_size_scan_fractions = _env_float_list(
            "FLASH_SIZE_SCAN_FRACTIONS",
            [0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20],
        )

        all_records = []
        for scan_no in range(1, max_scans + 1):
            gas_oracle.invalidate()
            gas_snap = gas_oracle.get_snapshot()
            tip_opt = TipOptimizer(gas_snap, gas_units=_GAS_UNITS, chain="polygon")
            pool_map = _discover_pools(w3)
            token_prices = _derive_token_prices_usd(pool_map)
            pool_map = _filter_pool_universe(pool_map, token_prices)

            for pair_key, pools in sorted(pool_map.items()):
                if len(pools) < 2:
                    continue
                for i in range(len(pools)):
                    for j in range(i + 1, len(pools)):
                        pa, pb = pools[i], pools[j]
                        buy, sell = (pa, pb) if pa.price >= pb.price else (pb, pa)
                        rec = _compute_opportunity(
                            scan_no, pair_key, buy, sell,
                            token_prices, sentinel, tip_opt, size,
                            flash_loan_fee_rate=flash_fee_rate,
                            min_flash_loan_usd=min_flash_loan_usd,
                            max_flash_loan_usd=max_flash_loan_usd,
                            max_flash_tvl_fraction=max_flash_tvl_fraction,
                            flash_size_scan_fractions=flash_size_scan_fractions,
                            min_net_profit_usd=min_profit,
                        )
                        if rec:
                            all_records.append(rec)
                            yield _sse_event(asdict(rec), event="opportunity")

            tri = _scan_triangular_cycles(
                scan_no, pool_map, token_prices, tip_opt,
                max_trade_size_usd=size,
                flash_loan_fee_rate=flash_fee_rate,
                min_net_profit_usd=min_profit,
            )
            for rec in tri:
                all_records.append(rec)
                yield _sse_event(asdict(rec), event="opportunity")

        profitable = [r for r in all_records if r.profitable]
        yield _sse_event({
            "total": len(all_records),
            "profitable_count": len(profitable),
            "sum_e_profit": round(sum(r.e_profit for r in all_records), 6),
            "max_net_edge": round(max((r.expected_net_edge for r in all_records), default=0.0), 6),
            "mode": "live",
        }, event="summary")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


@app.route("/api/pipeline")
def api_pipeline():
    """Run the SSOTPipelineFinalizer on a 2-leg pool state and return results.

    Query params (all float):
        r1_in, r1_out  Pool 1 reserves (buy leg)
        fee1           Pool 1 fee rate (decimal, e.g. 0.003)
        r2_in, r2_out  Pool 2 reserves (sell leg)
        fee2           Pool 2 fee rate
        c_total_exec   Owner submission cost in asset A. DEX fees are embedded
                       in AMM outputs; flash fees are route-token costs.
        p_fill         Fill probability for EV gate (default 0.9)
        n_batch        Batch simulation runs (default 100)
        sizes          Comma-separated candidate sizes (default auto-grid)
    """
    def _f(key: str, default: float) -> float:
        try:
            return float(request.args.get(key, default))
        except (ValueError, TypeError):
            return default

    r1_in        = _f("r1_in",        1_000_000.0)
    r1_out       = _f("r1_out",       1_000_000.0)
    fee1         = _f("fee1",         0.003)
    r2_in        = _f("r2_in",       1_000_000.0)
    r2_out       = _f("r2_out",       1_000_000.0)
    fee2         = _f("fee2",         0.003)
    c_total_exec = _f("c_total_exec", 0.0)
    p_fill       = _f("p_fill",       0.9)
    n_batch = max(1, min(500, int(request.args.get("n_batch", 100))))

    # Candidate size grid: honour explicit "sizes" param or build auto-grid
    sizes_raw = request.args.get("sizes", "")
    if sizes_raw:
        try:
            sizes = [float(x) for x in sizes_raw.split(",") if x.strip()]
        except ValueError:
            sizes = []
    if not sizes_raw or not sizes:
        # 20-point log grid from 1% to 100% of pool depth
        depth = min(r1_in, r2_out)
        sizes = [depth * (0.01 * (100.0 / 0.01) ** (i / 19.0)) for i in range(20)]
        sizes = [s for s in sizes if s > 0]

    try:
        from apex_omega_core.core.ssot_pipeline import SSOTPipelineFinalizer  # noqa: PLC0415
        from dataclasses import asdict as dc_asdict  # noqa: PLC0415

        finalizer = SSOTPipelineFinalizer(
            sizes_to_test=sizes,
            n_batch_runs=n_batch,
            p_fill=p_fill,
            rng_seed=42,
        )
        result = finalizer.run(
            fee1=fee1, r1_in=r1_in, r1_out=r1_out,
            fee2=fee2, r2_in=r2_in, r2_out=r2_out,
            c_total_exec=c_total_exec,
        )
        out = dc_asdict(result)
        out["inputs"] = {
            "r1_in": r1_in, "r1_out": r1_out, "fee1": fee1,
            "r2_in": r2_in, "r2_out": r2_out, "fee2": fee2,
            "c_total_exec": c_total_exec, "p_fill": p_fill, "n_batch": n_batch,
        }
        return jsonify(out)
    except ValueError as exc:
        return jsonify({"error": _safe_error(exc), "c2_decision": "DO_NOTHING"}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc)}), 500


@app.route("/api/routes")
def api_routes():
    """Return route records with raw-vs-after-math spread fields normalized."""
    if not _RESULTS_CSV.exists():
        return jsonify({"error": "No results file found. Run a scan first.", "records": []}), 404

    try:
        rows = _typed_csv_rows(_RESULTS_CSV)
        missing_price_count = 0
        for row in rows:
            spot = _safe_float(row.get("spot_spread_bps", row.get("raw_spread_bps", 0.0)))
            executable = _safe_float(
                row.get("executable_spread_bps", row.get("raw_spread_bps", 0.0))
            )
            row["raw_spread_before_math_bps"] = round(spot, 4)
            row["spread_after_math_bps"] = round(executable, 4)
            row["spread_math_delta_bps"] = round(spot - executable, 4)
            has_buy = row.get("buy_price_usdc") not in (None, "")
            has_sell = row.get("sell_price_usdc") not in (None, "")
            if not has_buy or not has_sell:
                missing_price_count += 1
                row["buy_price_usdc"] = None
                row["sell_price_usdc"] = None
                row["price_source"] = "legacy_csv_missing_prices"
            else:
                row["price_source"] = "dry_run_executable_prices"

        rows.sort(
            key=lambda r: (
                _safe_float(r.get("spread_after_math_bps")),
                _safe_float(r.get("expected_net_edge")),
            ),
            reverse=True,
        )
        return jsonify({
            "file": str(_RESULTS_CSV),
            "count": len(rows),
            "missing_price_count": missing_price_count,
            "price_columns_ready": missing_price_count == 0,
            "records": rows,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc)}), 500


@app.route("/api/live-blockers")
def api_live_blockers():
    try:
        from apex_omega_core.core.execution_dna import live_execution_blockers  # noqa: PLC0415

        blockers = live_execution_blockers()
        return jsonify({"count": len(blockers), "blockers": blockers})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc), "blockers": [_safe_error(exc)]}), 500


@app.route("/api/execution-dna")
def api_execution_dna():
    try:
        from apex_omega_core.core.execution_dna import (  # noqa: PLC0415
            build_execution_dna_cards,
            live_execution_blockers,
        )

        limit = max(1, min(20, int(request.args.get("limit", "20"))))
        cards = build_execution_dna_cards(limit=limit, csv_path=_RESULTS_CSV)
        return jsonify({
            "mode": "NO_BROADCAST_DRY_RUN",
            "count": len(cards),
            "requested": limit,
            "live_blockers": live_execution_blockers(),
            "cards": cards,
            "broadcast": {
                "enabled": False,
                "reason": "endpoint compiles payload metadata only; it never signs or submits transactions",
            },
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc), "cards": []}), 500


@app.route("/api/execution-dna/stream")
def api_execution_dna_stream():
    def generate():
        try:
            from apex_omega_core.core.execution_dna import (  # noqa: PLC0415
                build_execution_dna_cards,
                live_execution_blockers,
            )

            limit = max(1, min(20, int(request.args.get("limit", "20"))))
            yield _sse_event({"mode": "NO_BROADCAST_DRY_RUN", "live_blockers": live_execution_blockers()}, event="start")
            cards = build_execution_dna_cards(limit=limit, csv_path=_RESULTS_CSV)
            for card in cards:
                yield _sse_event(card, event="dna")
            yield _sse_event({"count": len(cards), "broadcast": False}, event="done")
        except Exception as exc:  # noqa: BLE001
            yield _sse_event({"message": _safe_error(exc)}, event="error")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_pool_price_rows(pool_map: Dict[str, List[Any]], quote_size_usd: float) -> List[Dict[str, Any]]:
    from dry_run import _derive_token_prices_usd, _pool_swap_out  # noqa: PLC0415

    token_prices = _derive_token_prices_usd(pool_map)
    rows: List[Dict[str, Any]] = []

    def _row_for_token(
        *,
        pool: Any,
        pair_key: str,
        token: str,
        direct_price_usdc: float,
        buy_quote_amount: float,
        buy_quote_price: float,
        buy_swap_0_to_1: bool,
        sell_token_amount: float,
        sell_quote_price: float,
        sell_swap_0_to_1: bool,
        reserve_in: float,
        reserve_out: float,
    ) -> Optional[Dict[str, Any]]:
        if direct_price_usdc <= 0 or buy_quote_amount <= 0 or sell_token_amount <= 0:
            return None
        token_out = _pool_swap_out(buy_quote_amount, pool, buy_swap_0_to_1)
        quote_out = _pool_swap_out(sell_token_amount, pool, sell_swap_0_to_1)
        if token_out <= 0 or quote_out <= 0:
            return None
        lowest_exec = (buy_quote_amount / token_out) * buy_quote_price
        highest_exec = (quote_out / sell_token_amount) * sell_quote_price
        return {
            "token": token,
            "quote_token": "USDC",
            "venue": pool.dex,
            "pair": pair_key,
            "pool": pool.pool_address,
            "kind": pool.kind,
            "fee": pool.fee,
            "direct_contract_price_usdc": round(direct_price_usdc, 10),
            "lowest_executable_usdc": round(lowest_exec, 10),
            "highest_executable_usdc": round(highest_exec, 10),
            "reserve_in": round(reserve_in, 8),
            "reserve_out": round(reserve_out, 8),
        }

    for pair_key, pools in sorted(pool_map.items()):
        for pool in pools:
            if pool.price <= 0 or pool.reserve0 <= 0 or pool.reserve1 <= 0:
                continue
            price0 = token_prices.get(pool.sym0, 0.0)
            price1 = token_prices.get(pool.sym1, 0.0)
            if price0 <= 0 or price1 <= 0:
                continue

            direct0 = pool.price * price1
            direct1 = price0 / pool.price

            buy0_quote_amt = min(quote_size_usd / price1, pool.reserve1 * 0.01)
            sell0_amt = min(quote_size_usd / direct0, pool.reserve0 * 0.01)
            row0 = _row_for_token(
                pool=pool,
                pair_key=pair_key,
                token=pool.sym0,
                direct_price_usdc=direct0,
                buy_quote_amount=buy0_quote_amt,
                buy_quote_price=price1,
                buy_swap_0_to_1=False,
                sell_token_amount=sell0_amt,
                sell_quote_price=price1,
                sell_swap_0_to_1=True,
                reserve_in=pool.reserve1,
                reserve_out=pool.reserve0,
            )
            if row0:
                rows.append(row0)

            buy1_quote_amt = min(quote_size_usd / price0, pool.reserve0 * 0.01)
            sell1_amt = min(quote_size_usd / direct1, pool.reserve1 * 0.01)
            row1 = _row_for_token(
                pool=pool,
                pair_key=pair_key,
                token=pool.sym1,
                direct_price_usdc=direct1,
                buy_quote_amount=buy1_quote_amt,
                buy_quote_price=price0,
                buy_swap_0_to_1=True,
                sell_token_amount=sell1_amt,
                sell_quote_price=price0,
                sell_swap_0_to_1=False,
                reserve_in=pool.reserve0,
                reserve_out=pool.reserve1,
            )
            if row1:
                rows.append(row1)

    return rows


@app.route("/api/token-prices")
def api_token_prices():
    """Return live token prices by venue with executable buy/sell prices."""
    try:
        from dry_run import _discover_pools, _filter_pool_universe, _derive_token_prices_usd  # noqa: PLC0415
        from web3 import Web3  # noqa: PLC0415

        quote_size_usd = max(1.0, _safe_float(request.args.get("size"), 10_000.0))
        sort_mode = request.args.get("sort", "lowest")
        rpc = os.getenv("POLYGON_RPC", _DEFAULT_RPC)
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        if not w3.is_connected():
            return jsonify({"error": "Cannot reach Polygon RPC. Set POLYGON_RPC and restart.", "records": []}), 503

        pool_map = _discover_pools(w3, max_workers=24)
        token_prices = _derive_token_prices_usd(pool_map)
        pool_map = _filter_pool_universe(pool_map, token_prices)
        rows = _build_pool_price_rows(pool_map, quote_size_usd)

        if sort_mode == "highest":
            rows.sort(key=lambda r: _safe_float(r.get("highest_executable_usdc")), reverse=True)
        elif sort_mode == "venue":
            rows.sort(key=lambda r: (str(r.get("venue", "")), str(r.get("token", ""))))
        elif sort_mode == "token_low":
            rows.sort(key=lambda r: (str(r.get("token", "")), _safe_float(r.get("lowest_executable_usdc"))))
        else:
            rows.sort(key=lambda r: _safe_float(r.get("lowest_executable_usdc")))

        return jsonify({
            "count": len(rows),
            "quote_size_usd": quote_size_usd,
            "sort": sort_mode,
            "records": rows,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc), "records": []}), 500


@app.route("/api/results")
def api_results():
    """Return the last dry-run CSV as a JSON array of records."""
    if not _RESULTS_CSV.exists():
        return jsonify({"error": "No results file found. Run a scan first.", "records": []}), 404
    try:
        rows = _typed_csv_rows(_RESULTS_CSV)
        return jsonify({"file": str(_RESULTS_CSV), "count": len(rows), "records": rows})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc)}), 500




@app.route("/api/feeds")
def api_feeds():
    """Poll all four live data feeds and return their status + arb signals.

    This endpoint makes real network requests to:
      - The Graph (Uniswap V3 Polygon subgraph)
      - CoinGecko free price API
      - PolygonScan gas oracle
      - Polygon RPC (block number + gas price)

    Returns
    -------
    JSON with keys:
      feeds           dict[name -> {status, latency_ms, error, fetched_at}]
      pools           list of pool reserve snapshots from The Graph
      token_prices_usd  dict[symbol -> USD price] from CoinGecko
      gas_base_fee_gwei / gas_safe_gwei / gas_fast_gwei  from PolygonScan
      block_number / rpc_gas_price_gwei  from Polygon RPC
      arb_signals     CPMM arbitrage signals computed from live reserves
      all_live        bool — True when all four feeds are LIVE
    """
    from apex_omega_core.core.live_data_feeds import LiveDataFeeds  # noqa: PLC0415
    from dataclasses import asdict as _asdict  # noqa: PLC0415

    rpc = os.getenv("POLYGON_RPC", _DEFAULT_RPC)
    ldf = LiveDataFeeds(rpc_url=rpc)
    snapshot = asyncio.run(ldf.poll())

    return jsonify({
        "timestamp": snapshot.timestamp,
        "all_live": snapshot.all_live,
        "feeds": {k: v.to_dict() for k, v in snapshot.feeds.items()},
        "pools": [_asdict(p) for p in snapshot.pools],
        "token_prices_usd": snapshot.token_prices_usd,
        "gas_base_fee_gwei": snapshot.gas_base_fee_gwei,
        "gas_safe_gwei": snapshot.gas_safe_gwei,
        "gas_fast_gwei": snapshot.gas_fast_gwei,
        "block_number": snapshot.block_number,
        "rpc_gas_price_gwei": snapshot.rpc_gas_price_gwei,
        "arb_signals": [_asdict(s) for s in snapshot.arb_signals],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
