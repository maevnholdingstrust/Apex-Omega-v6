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
GET  /api/results               Last dry-run CSV as JSON records
"""

from __future__ import annotations

import asyncio
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
    "apex_omega_core.strategies.execution_router",
]

_DEFAULT_RPC = "https://polygon.drpc.org"
_RESULTS_CSV = ROOT / "dry_run_results.csv"

# ---------------------------------------------------------------------------
# Live-feed singleton + TTL cache
# ---------------------------------------------------------------------------
# A single LiveDataFeeds instance is reused across all requests so Web3
# connections, last-known-good feed state, and the in-memory snapshot cache
# persist between calls.  The cache ensures external APIs are hit at most
# once per APEX_FEED_CACHE_TTL_S (default 30 s) regardless of how often the
# browser polls /api/feeds.

try:
    from apex_omega_core.core.live_data_feeds import LiveDataFeeds as _LiveDataFeeds
    _LiveDataFeeds_type = _LiveDataFeeds
except Exception:  # noqa: BLE001
    _LiveDataFeeds_type = None  # type: ignore[assignment,misc]

_ldf_instance: Optional["_LiveDataFeeds"] = None  # type: ignore[name-defined]


def _get_ldf() -> Any:
    """Return (or lazily create) the module-level LiveDataFeeds singleton."""
    global _ldf_instance  # noqa: PLW0603
    if _ldf_instance is None:
        if _LiveDataFeeds_type is None:
            from apex_omega_core.core.live_data_feeds import LiveDataFeeds  # noqa: PLC0415
            cls = LiveDataFeeds
        else:
            cls = _LiveDataFeeds_type
        rpc = os.getenv("POLYGON_RPC", _DEFAULT_RPC)
        _ldf_instance = cls(rpc_url=rpc)
    return _ldf_instance


def _get_feeds_snapshot() -> Any:
    """Return a feed snapshot, using the server-side TTL cache.

    Calls ``poll_cached()`` on the singleton so that:
    - the first call hits all external APIs and warms the cache;
    - subsequent calls within APEX_FEED_CACHE_TTL_S (default 30 s) return
      the cached result instantly without any network traffic;
    - when an external API fails, STALE data from the last successful poll
      is returned so the dashboard is never empty.
    """
    ldf = _get_ldf()
    return asyncio.run(ldf.poll_cached())

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


def _readiness_metrics(
    feeds: Dict[str, Any], chain_states: Dict[str, Any]
) -> Dict[str, Any]:
    """Compute real-time readiness percentages from feed/chain status."""
    statuses = [v.status for v in feeds.values()] + [v.status for v in chain_states.values()]
    total = len(statuses)
    if total == 0:
        return {
            "up_to_date_pct": 0.0,
            "operational_pct": 0.0,
            "up_to_date_components": 0,
            "operational_components": 0,
            "total_components": 0,
        }
    up_to_date = sum(1 for s in statuses if s == "LIVE")
    operational = sum(1 for s in statuses if s in ("LIVE", "STALE"))
    return {
        "up_to_date_pct": round((up_to_date / total) * 100.0, 1),
        "operational_pct": round((operational / total) * 100.0, 1),
        "up_to_date_components": up_to_date,
        "operational_components": operational,
        "total_components": total,
    }


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


def _sse_event(data: Any, event: Optional[str] = None) -> str:
    payload = json.dumps(data) if not isinstance(data, str) else data
    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {payload}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


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
  .feed-stale { color: var(--yellow); }
  .feed-error { color: var(--red); }
  .feed-meta  { font-size: .75rem; color: var(--muted); margin-top: .2rem; }
  .feed-error-msg { font-size: .72rem; color: var(--red); margin-top: .15rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
  #feeds-updated { font-size: .78rem; color: var(--muted); margin-bottom: .4rem; }
  .arb-row-pos { color: var(--green); }
  .arb-row-zero { color: var(--muted); }
  /* ── Chain RPC grid ── */
  .chain-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .6rem; margin-bottom: .75rem; }
  .chain-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: .6rem .85rem; }
  .chain-label { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
  .chain-block { font-size: .88rem; font-weight: 600; margin-top: .15rem; }
  .chain-gas   { font-size: .75rem; color: var(--muted); }
  .stale-badge { display: inline-block; margin-left: .35rem; padding: .05rem .35rem;
                 background: #3d2e00; color: var(--yellow); border-radius: 8px;
                 font-size: .68rem; font-weight: 700; vertical-align: middle; }
</style>
</head>
<body>
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

<div id="chain-section" style="display:none">
  <h3 style="font-size:.85rem;color:var(--muted);margin:.75rem 0 .35rem">Chain RPC Status</h3>
  <div class="chain-grid" id="chain-cards"><!-- populated by JS --></div>
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

<script>
// ── Live Data Feeds ───────────────────────────────────────────────────────────
const FEED_LABELS = {
  the_graph:    'The Graph (Uniswap V3)',
  coingecko:    'CoinGecko Prices',
  etherscan_gas:'PolygonScan Gas Oracle',
  polygon_rpc:  'Polygon RPC',
};

let feedsEvt = null;
let feedsPollTimer = null;

function statusClass(status) {
  if (status === 'LIVE')  return 'feed-live';
  if (status === 'STALE') return 'feed-stale';
  return 'feed-error';
}

function staleTag(status, ageS) {
  if (status === 'STALE')
    return `<span class="stale-badge" title="Using last-known-good data">STALE</span>`;
  if (status === 'LIVE' && ageS > 0)
    return `<span class="stale-badge" title="Served from server cache" style="background:#1a2a1a;color:var(--green)">CACHED</span>`;
  return '';
}

function renderChains(chainStates, ageS) {
  const section = document.getElementById('chain-section');
  const container = document.getElementById('chain-cards');
  if (!chainStates || !Object.keys(chainStates).length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';
  container.innerHTML = '';
  for (const [slug, cs] of Object.entries(chainStates)) {
    const isOk = cs.status === 'LIVE' || cs.status === 'STALE';
    const blockTxt = cs.block_number
      ? cs.block_number.toLocaleString()
      : (isOk ? '—' : 'ERR');
    const gasTxt = cs.gas_price_gwei != null
      ? `${cs.gas_price_gwei.toFixed(1)} gwei`
      : '';
    const errLine = (!isOk && cs.error)
      ? `<div class="feed-error-msg" title="${cs.error}">${cs.error}</div>` : '';
    container.innerHTML += `
      <div class="chain-card">
        <div class="chain-label">${cs.label || slug}${staleTag(cs.status, ageS)}</div>
        <div class="chain-block ${isOk ? 'feed-live' : 'feed-error'}">
          ${isOk ? '● ' : '✗ '}Block ${blockTxt}
        </div>
        ${gasTxt ? `<div class="chain-gas">${gasTxt}  •  ${cs.latency_ms ? cs.latency_ms.toFixed(0)+'ms' : ''}</div>` : ''}
        ${errLine}
      </div>`;
  }
}

function renderFeeds(data) {
  const cards = document.getElementById('feed-cards');
  const upd = document.getElementById('feeds-updated');
  const ts = new Date(data.timestamp * 1000).toLocaleTimeString();
  const ageS = data.age_s || 0;
  const cachedNote = data.cached ? ` (cached ${ageS.toFixed(0)}s ago)` : ' (fresh)';
  const upToDatePct = Number(data.readiness?.up_to_date_pct ?? 0).toFixed(1);
  const operationalPct = Number(data.readiness?.operational_pct ?? 0).toFixed(1);
  upd.textContent = `Last polled: ${ts}${cachedNote}  |  Up-to-date readiness: ${upToDatePct}%  |  Operational readiness: ${operationalPct}%  |  All live: ${data.all_live ? '✓' : '⚠'}`;

  cards.innerHTML = '';
  for (const [key, state] of Object.entries(data.feeds || {})) {
    const isOk = state.status === 'LIVE' || state.status === 'STALE';
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
      const eth = prices['WETH'] || prices['ETH'] || 0;
      const parts = [];
      if (pol) parts.push(`POL $${pol.toFixed(3)}`);
      if (eth) parts.push(`ETH $${eth.toFixed(0)}`);
      meta = parts.join('  •  ');
    }
    const errLine = (!isOk && state.error)
      ? `<div class="feed-error-msg" title="${state.error}">${state.error}</div>` : '';
    const latency = isOk ? `${(state.latency_ms||0).toFixed(0)} ms` : '';
    cards.innerHTML += `
      <div class="feed-card">
        <div class="feed-name">${label}${staleTag(state.status, ageS)}</div>
        <div class="feed-status ${statusClass(state.status)}">${state.status}</div>
        ${meta ? `<div class="feed-meta">${meta}</div>` : ''}
        ${latency ? `<div class="feed-meta">${latency}</div>` : ''}
        ${errLine}
      </div>`;
  }

  // Per-chain RPC status cards
  renderChains(data.chain_states, ageS);

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
    const anyStale = Object.values(data.feeds||{}).some(f => f.status === 'STALE')
      || Object.values(data.chain_states||{}).some(c => c.status === 'STALE');
    const upToDatePct = Number(data.readiness?.up_to_date_pct ?? 0).toFixed(1);
    if (data.all_live && !anyStale)
      statusEl.textContent = data.cached ? `✓ All feeds LIVE (${upToDatePct}% up-to-date, cached)` : `✓ All feeds LIVE (${upToDatePct}% up-to-date)`;
    else if (anyStale)
      statusEl.textContent = `⚠ Some feeds STALE — serving last known-good data (${upToDatePct}% up-to-date)`;
    else
      statusEl.textContent = `⚠ Feed error — check cards (${upToDatePct}% up-to-date)`;
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
    ok = all(m["ok"] for m in mods) and rust["available"]
    return jsonify({
        "ok": ok,
        "rust_core": rust["available"],
        "modules_loaded": sum(1 for m in mods if m["ok"]),
        "modules_total": len(mods),
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
    return jsonify({
        "rust_core": rust,
        "chain": chain,
        "modules": mods,
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
        c_total_exec   Execution-external costs in asset A: flash_fee + gas_cost
                       ONLY (DEX fees are embedded in AMM outputs). Default 0.0.
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


@app.route("/api/results")
def api_results():
    """Return the last dry-run CSV as a JSON array of records."""
    if not _RESULTS_CSV.exists():
        return jsonify({"error": "No results file found. Run a scan first.", "records": []}), 404
    import csv  # noqa: PLC0415
    try:
        with open(_RESULTS_CSV, newline="") as fh:
            reader = csv.DictReader(fh)
            rows = []
            for row in reader:
                typed: Dict[str, Any] = {}
                for k, v in row.items():
                    try:
                        typed[k] = float(v) if "." in v else int(v)
                    except (ValueError, TypeError):
                        typed[k] = v
                rows.append(typed)
        return jsonify({
            "file": str(_RESULTS_CSV),
            "count": len(rows),
            "records": rows,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _safe_error(exc)}), 500




@app.route("/api/feeds")
def api_feeds():
    """Return live feed status + arb signals, served from the server-side cache.

    External APIs (The Graph, CoinGecko, PolygonScan, chain RPCs) are polled
    at most once per ``APEX_FEED_CACHE_TTL_S`` (default 30 s).  When a feed
    is temporarily unreachable, last-known-good data is returned with
    ``status="STALE"`` so the dashboard always has something to display.

    Returns
    -------
    JSON with keys:
      feeds           dict[name -> {status, latency_ms, error, fetched_at}]
      chain_states    dict[chain -> ChainRpcState] for all active chains
      pools           list of pool reserve snapshots from The Graph
      token_prices_usd  dict[symbol -> USD price] from CoinGecko (shared)
      gas_base_fee_gwei / gas_safe_gwei / gas_fast_gwei  from PolygonScan
      block_number / rpc_gas_price_gwei  from primary Polygon RPC
      arb_signals     CPMM arbitrage signals computed from live reserves
      all_live        bool — True when all feeds are LIVE or STALE
      readiness       dict with up-to-date/operational readiness percentages
      age_s           seconds since the snapshot was polled from source
      cached          bool — True when response came from the TTL cache
    """
    from dataclasses import asdict as _asdict  # noqa: PLC0415

    snapshot = _get_feeds_snapshot()
    readiness = _readiness_metrics(snapshot.feeds, snapshot.chain_states or {})

    return jsonify({
        "timestamp": snapshot.timestamp,
        "age_s": snapshot.age_s,
        "cached": snapshot.from_cache,
        "all_live": snapshot.all_live,
        "readiness": readiness,
        "feeds": {k: v.to_dict() for k, v in snapshot.feeds.items()},
        "chain_states": {
            k: v.to_dict() for k, v in (snapshot.chain_states or {}).items()
        },
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
