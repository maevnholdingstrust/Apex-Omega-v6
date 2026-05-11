"""Dry-run dashboard API server.

Windows-native, no Flask/FastAPI dependency required.
Serves Apex-Omega dry-run DNA logs over HTTP.

Endpoints:
  GET /api/dry-run/status
  GET /api/dry-run/dna-cards
  GET /api/dry-run/cycle-pairs
  GET /api/dry-run/block-cycles
  GET /api/dry-run/payloads
  GET /api/dry-run/rejections
  GET /api/dry-run/summary
  GET /api/dry-run/events
  GET /api/dry-run/events/stream
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


LOG_FILES = {
    "dna_cards": "dry_run_dna_cards.jsonl",
    "cycle_pairs": "dry_run_cycle_pairs.jsonl",
    "block_cycles": "dry_run_block_cycles.jsonl",
    "payloads": "dry_run_payload_builds.jsonl",
    "rejections": "dry_run_rejections.jsonl",
    "events": "dry_run_dashboard_events.jsonl",
    "summary": "dry_run_summary.json",
}

RUNNER_LOCK = threading.Lock()
RUNNER_STATE: dict[str, Any] = {
    "running": False,
    "pid": None,
    "started_at": None,
    "ended_at": None,
    "exit_code": None,
    "limit": None,
    "log_path": None,
    "last_error": None,
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"failed_to_read_json: {exc}", "path": str(path)}


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_parse_error": True, "raw": line})

    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


def _safe_int(value: str | None, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _configured_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            return default

    for env_path in (Path.cwd() / ".env", Path.cwd() / "runtime" / "active_endpoints.env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != name:
                continue
            try:
                return float(value.strip().strip('"').strip("'"))
            except ValueError:
                return default
    return default


def build_status(log_dir: Path) -> dict[str, Any]:
    cards = _read_jsonl(log_dir / LOG_FILES["dna_cards"])
    pairs = _read_jsonl(log_dir / LOG_FILES["cycle_pairs"])
    blocks = _read_jsonl(log_dir / LOG_FILES["block_cycles"])
    rejections = _read_jsonl(log_dir / LOG_FILES["rejections"])
    summary = _read_json(log_dir / LOG_FILES["summary"], {})

    c1_cards = [
        c for c in cards
        if c.get("strike_role") == "C1"
        or c.get("identity", {}).get("strike_role") == "C1"
    ]
    c2_cards = [
        c for c in cards
        if c.get("strike_role") == "C2"
        or c.get("identity", {}).get("strike_role") == "C2"
    ]
    limit = int(summary.get("limit") or len(pairs) or 0)
    c2_execute = len([p for p in pairs if p.get("c2_decision") == "EXECUTE"])
    c2_no_op = len([p for p in pairs if p.get("c2_decision") == "NO_OP"])
    simulated_net_values = [
        float(p.get("simulated_net_usd") or 0.0)
        for p in pairs
    ]
    total_simulated_net = sum(simulated_net_values)
    avg_simulated_net = total_simulated_net / len(simulated_net_values) if simulated_net_values else 0.0
    best_simulated_net = max(simulated_net_values) if simulated_net_values else 0.0

    return {
        "status": summary.get("status", "unknown"),
        "limit": limit,
        "dry_run_mode": True,
        "broadcast_enabled": False,
        "realized_status": summary.get("realized_status", "DRY_RUN_NO_BROADCAST"),
        "realized_net_opportunity_usd": summary.get("realized_net_opportunity_usd"),
        "cycles_completed": summary.get("cycles_completed", len(pairs)),
        "cycle_pairs": len(pairs),
        "total_dna_cards": len(cards),
        "c1_cards": len(c1_cards),
        "c2_cards": len(c2_cards),
        "block_summaries": len(blocks),
        "rejections": len(rejections),
        "c2_execute": c2_execute,
        "c2_no_op": c2_no_op,
        "total_simulated_net_usd": total_simulated_net,
        "avg_simulated_net_usd": avg_simulated_net,
        "best_simulated_net_usd": best_simulated_net,
        "min_c1_profit_usd": _configured_float("DRY_RUN_LIVE_MIN_C1_PROFIT_USD", 2.0),
        "min_c2_profit_usd": _configured_float("DRY_RUN_LIVE_MIN_C2_PROFIT_USD", 2.0),
        "log_dir": str(log_dir),
        "expected_run_pass": bool(limit) and len(pairs) == limit and len(c1_cards) == limit and len(c2_cards) == limit,
        "expected_first20_pass": limit == 20 and len(pairs) == 20 and len(c1_cards) == 20 and len(c2_cards) == 20 and len(cards) == 40,
    }


def _runner_snapshot() -> dict[str, Any]:
    with RUNNER_LOCK:
        return dict(RUNNER_STATE)


def _runner_log_path() -> Path:
    runtime_dir = Path.cwd() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / "dry_run_dashboard_runner.log"


def _tail_text(path: Path, max_chars: int = 6000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _start_live_dry_run(limit: int) -> dict[str, Any]:
    limit = max(1, min(50, int(limit)))
    with RUNNER_LOCK:
        if RUNNER_STATE.get("running"):
            return {"accepted": False, "reason": "RUN_ALREADY_ACTIVE", **dict(RUNNER_STATE)}

        log_path = _runner_log_path()
        log_path.write_text("", encoding="utf-8")
        env = os.environ.copy()
        python_path = str(Path.cwd() / "python")
        env["PYTHONPATH"] = (
            python_path
            if not env.get("PYTHONPATH")
            else python_path + os.pathsep + env["PYTHONPATH"]
        )
        cmd = [
            sys.executable,
            "-m",
            "apex_omega_core.dry_run.run_first20_dna_dry_run",
            "--limit",
            str(limit),
            "--dashboard-stream",
            "--no-broadcast",
        ]
        log_fh = log_path.open("a", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            cmd,
            cwd=Path.cwd(),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        RUNNER_STATE.update(
            {
                "running": True,
                "pid": proc.pid,
                "started_at": datetime.now().isoformat(),
                "ended_at": None,
                "exit_code": None,
                "limit": limit,
                "log_path": str(log_path),
                "last_error": None,
            }
        )

    def wait_for_runner() -> None:
        exit_code = proc.wait()
        log_fh.close()
        with RUNNER_LOCK:
            RUNNER_STATE.update(
                {
                    "running": False,
                    "ended_at": datetime.now().isoformat(),
                    "exit_code": exit_code,
                    "last_error": None if exit_code == 0 else f"dry_run_exit_code_{exit_code}",
                }
            )

    threading.Thread(target=wait_for_runner, daemon=True).start()
    return {"accepted": True, **_runner_snapshot()}


def make_handler(log_dir: Path):
    class DryRunDashboardHandler(BaseHTTPRequestHandler):
        server_version = "ApexOmegaDryRunDashboard/1.0"

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _query_limit(self) -> int | None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            limit_values = query.get("limit")
            if not limit_values:
                return None
            return _safe_int(limit_values[0], None)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            limit = self._query_limit()

            if path == "/":
                return self._send_html(self._index_html())

            if path == "/api/dry-run/status":
                return self._send_json(build_status(log_dir))

            if path == "/api/dry-run/run-live":
                query = parse_qs(parsed.query)
                run_limit = _safe_int(query.get("limit", [None])[0], 17) or 17
                return self._send_json(_start_live_dry_run(run_limit))

            if path == "/api/dry-run/runner":
                payload = _runner_snapshot()
                log_path = Path(str(payload.get("log_path") or _runner_log_path()))
                payload["log_tail"] = _tail_text(log_path)
                return self._send_json(payload)

            if path == "/api/dry-run/dna-cards":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["dna_cards"], limit))

            if path.startswith("/api/dry-run/dna-cards/"):
                cycle_id = path.split("/")[-1]
                cards = _read_jsonl(log_dir / LOG_FILES["dna_cards"])
                matched = [
                    c for c in cards
                    if c.get("cycle_id") == cycle_id
                    or c.get("identity", {}).get("cycle_id") == cycle_id
                    or c.get("card_id") == cycle_id
                    or c.get("identity", {}).get("card_id") == cycle_id
                ]
                return self._send_json(matched)

            if path == "/api/dry-run/cycle-pairs":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["cycle_pairs"], limit))

            if path == "/api/dry-run/block-cycles":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["block_cycles"], limit))

            if path == "/api/dry-run/payloads":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["payloads"], limit))

            if path == "/api/dry-run/rejections":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["rejections"], limit))

            if path == "/api/dry-run/events":
                return self._send_json(_read_jsonl(log_dir / LOG_FILES["events"], limit))

            if path == "/api/dry-run/summary":
                return self._send_json(_read_json(log_dir / LOG_FILES["summary"], {}))

            if path == "/api/dry-run/events/stream":
                return self._send_sse_events(log_dir / LOG_FILES["events"])

            return self._send_json({"error": "not_found", "path": path}, status=404)

        def _send_sse_events(self, events_path: Path) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            rows = _read_jsonl(events_path, limit=200)
            for row in rows:
                payload = json.dumps(row, sort_keys=True)
                self.wfile.write(f"event: dry_run_event\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()

            # Keep connection briefly alive for clients that expect a stream.
            for _ in range(5):
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                time.sleep(1)

        def _index_html(self) -> str:
            return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Apex-Omega Dry-Run Dashboard</title>
  <style>
    :root {
      --bg: #090b0f;
      --rail: #0d1117;
      --panel: #111720;
      --panel-2: #151d28;
      --panel-3: #1b2531;
      --border: #2a3646;
      --text: #d7e1ee;
      --muted: #8795a8;
      --green: #37d67a;
      --red: #ff5e73;
      --blue: #5cb6ff;
      --amber: #ffd166;
      --violet: #b18cff;
    }
    * { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      overflow-x: hidden;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 22px;
      border-bottom: 1px solid var(--border);
      background: var(--rail);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 { margin: 0; font-size: 18px; letter-spacing: .02em; }
    h2 { margin: 0 0 12px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
    main { padding: 18px 22px 28px; display: grid; gap: 16px; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      color: var(--muted);
      background: var(--panel);
      font-size: 12px;
      white-space: nowrap;
    }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--green); box-shadow: 0 0 12px var(--green); }
    .topline { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    .hero-grid { display: grid; grid-template-columns: minmax(280px, 1.25fr) minmax(280px, .9fr) minmax(280px, .9fr); gap: 14px; }
    .metric-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 12px; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(160px, 1fr)); gap: 12px; }
    .card, .section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }
    .hero-card { min-height: 230px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .value { font: 700 22px ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }
    .value-sm { font: 700 15px ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; line-height: 1.35; }
    .subvalue { color: var(--muted); font-size: 12px; margin-top: 6px; }
    .ok { color: var(--green); }
    .warn { color: var(--amber); }
    .err { color: var(--red); }
    .blue { color: var(--blue); }
    .violet { color: var(--violet); }
    canvas { width: 100%; height: 150px; display: block; }
    .chart-tall { height: 190px; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .pill-row { display: flex; flex-wrap: wrap; gap: 8px; }
    .pill { border: 1px solid var(--border); background: var(--panel-2); border-radius: 999px; padding: 6px 9px; font-size: 12px; color: var(--muted); }
    .control-row { display: flex; align-items: end; gap: 10px; flex-wrap: wrap; }
    .field { display: grid; gap: 5px; }
    label { color: var(--muted); font-size: 12px; }
    input {
      width: 88px;
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 9px;
      font: 700 14px ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    button {
      background: var(--green);
      color: #07100b;
      border: 0;
      border-radius: 6px;
      padding: 9px 12px;
      font-weight: 800;
      cursor: pointer;
    }
    button:disabled { background: var(--panel-3); color: var(--muted); cursor: wait; }
    .log-box { margin-top: 12px; background: #080c11; border: 1px solid var(--border); border-radius: 6px; padding: 10px; max-height: 150px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; background: var(--panel-2); position: sticky; top: 0; }
    tbody tr:hover { background: #ffffff06; }
    .table-wrap { max-height: 440px; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--blue); }
    .links { display: flex; flex-wrap: wrap; gap: 8px; }
    a { color: var(--blue); text-decoration: none; font-size: 12px; border: 1px solid var(--border); padding: 6px 8px; border-radius: 6px; background: var(--panel); }
    pre { margin: 0; white-space: pre-wrap; color: var(--muted); font-size: 12px; max-height: 120px; overflow: auto; }
    @media (max-width: 1200px) {
      .hero-grid { grid-template-columns: 1fr; }
      .metric-grid { grid-template-columns: repeat(3, minmax(120px, 1fr)); }
    }
    @media (max-width: 760px) {
      header { align-items: flex-start; flex-direction: column; }
      .metric-grid, .grid, .split { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Apex-Omega Dry-Run Dashboard</h1>
      <div class="label">Live Polygon candidate run artifacts from JSONL logs</div>
    </div>
    <div class="topline">
      <div class="badge"><span class="dot"></span><span>DRY RUN / NO BROADCAST</span><span id="updated"></span></div>
      <div class="badge">C1 gate <strong id="c1-gate">$--</strong></div>
      <div class="badge">C2 gate <strong id="c2-gate">$--</strong></div>
    </div>
  </header>
  <main>
    <section class="metric-grid">
      <div class="card"><div class="label">Status</div><div class="value" id="status-value">--</div></div>
      <div class="card"><div class="label">Run Progress</div><div class="value" id="cycles">--</div><div class="subvalue" id="run-pass">--</div></div>
      <div class="card"><div class="label">DNA Cards</div><div class="value" id="cards">--</div></div>
      <div class="card"><div class="label">C2 Split</div><div class="value" id="c2split">--</div><div class="subvalue">execute / no-op</div></div>
      <div class="card"><div class="label">Best Cycle Net</div><div class="value" id="best-net">--</div></div>
      <div class="card"><div class="label">Total Sim Net</div><div class="value" id="total-net">--</div></div>
    </section>

    <section class="hero-grid">
      <div class="section hero-card">
        <h2>Profit Curve</h2>
        <canvas id="profit-chart" class="chart-tall"></canvas>
        <div class="pill-row" id="profit-pills"></div>
      </div>
      <div class="section hero-card">
        <h2>C2 Decision Split</h2>
        <canvas id="split-chart"></canvas>
        <div class="pill-row" id="split-pills"></div>
      </div>
      <div class="section hero-card">
        <h2>Live Runner</h2>
        <div class="control-row">
          <div class="field">
            <label for="run-limit">Cycle limit</label>
            <input id="run-limit" type="number" min="1" max="50" value="17">
          </div>
          <div class="field">
            <label for="auto-interval">Auto seconds</label>
            <input id="auto-interval" type="number" min="15" max="600" value="60">
          </div>
          <button id="run-live">Run Live Dry Run</button>
          <button id="auto-live" type="button">Auto Live: ON</button>
          <span class="pill" id="runner-state">idle</span>
        </div>
        <div class="pill-row" id="runner-pills" style="margin-top:10px"></div>
        <div class="subvalue" id="auto-countdown">next auto run pending</div>
        <pre class="log-box" id="runner-log"></pre>
      </div>
    </section>

    <section class="section">
      <h2>Safety</h2>
      <div id="safety" class="grid"></div>
    </section>

    <section class="section">
      <h2>C1/C2 Cycle Pairs</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Global</th><th>Block</th><th>Cycle</th><th>C1</th><th>C2</th><th>C1 Net</th><th>C2 Net</th><th>Total</th><th>Realized</th>
            </tr>
          </thead>
          <tbody id="pairs"></tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <h2>Payload Builds</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Opportunity</th><th>Cycle</th><th>Strike</th><th>Would Sign</th><th>Would Broadcast</th></tr></thead>
          <tbody id="payloads"></tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <h2>Recent Events</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Event</th><th>Opportunity</th><th>Details</th></tr></thead>
          <tbody id="events"></tbody>
        </table>
      </div>
    </section>

    <section class="links">
      <a href="/api/dry-run/status">status json</a>
      <a href="/api/dry-run/dna-cards">dna cards json</a>
      <a href="/api/dry-run/cycle-pairs">cycle pairs json</a>
      <a href="/api/dry-run/payloads">payloads json</a>
      <a href="/api/dry-run/events">events json</a>
      <a href="/api/dry-run/events/stream">events stream</a>
    </section>
  </main>
  <script>
    const money = v => v === null || v === undefined ? '--' : '$' + Number(v).toFixed(2);
    const money4 = v => v === null || v === undefined ? '--' : '$' + Number(v).toFixed(4);
    const text = v => v === null || v === undefined ? '--' : String(v);
    const boolClass = v => v ? 'ok' : 'err';
    const decisionClass = v => String(v).includes('EXECUTE') || String(v).includes('BUILD') ? 'ok' : 'warn';
    const td = (value, cls='') => `<td class="${cls}">${value}</td>`;
    const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    let autoLiveEnabled = true;
    let nextAutoRunAt = Date.now() + 2500;
    let autoRunInFlight = false;
    async function json(path) {
      const r = await fetch(path, { cache: 'no-store' });
      return await r.json();
    }
    function resizeCanvas(canvas) {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { ctx, w: rect.width, h: rect.height };
    }
    function drawProfitChart(pairs) {
      const canvas = document.getElementById('profit-chart');
      const { ctx, w, h } = resizeCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      const values = pairs.map(p => Number(p.simulated_net_usd || 0));
      if (!values.length) return;
      const max = Math.max(...values, 1);
      const min = Math.min(...values, 0);
      const pad = 22;
      ctx.strokeStyle = css('--border');
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i++) {
        const y = pad + (h - pad * 2) * i / 3;
        ctx.beginPath();
        ctx.moveTo(pad, y);
        ctx.lineTo(w - pad, y);
        ctx.stroke();
      }
      const bw = (w - pad * 2) / values.length;
      values.forEach((v, i) => {
        const x = pad + i * bw + 2;
        const bh = ((v - min) / (max - min || 1)) * (h - pad * 2);
        const y = h - pad - bh;
        ctx.fillStyle = v >= 5 ? css('--green') : v >= 2 ? css('--amber') : css('--red');
        ctx.fillRect(x, y, Math.max(4, bw - 4), bh);
      });
      ctx.fillStyle = css('--muted');
      ctx.font = '12px ui-monospace, Consolas, monospace';
      ctx.fillText(`max ${money(max)}`, pad, 14);
    }
    function drawSplitChart(status) {
      const canvas = document.getElementById('split-chart');
      const { ctx, w, h } = resizeCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      const execute = Number(status.c2_execute || 0);
      const noop = Number(status.c2_no_op || 0);
      const total = Math.max(1, execute + noop);
      const cx = w / 2;
      const cy = h / 2 + 6;
      const r = Math.min(w, h) * 0.34;
      let start = -Math.PI / 2;
      [
        [execute, css('--green')],
        [noop, css('--amber')],
      ].forEach(([value, color]) => {
        const end = start + (value / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, start, end);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
        start = end;
      });
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.58, 0, Math.PI * 2);
      ctx.fillStyle = css('--panel');
      ctx.fill();
      ctx.fillStyle = css('--text');
      ctx.font = '700 24px ui-monospace, Consolas, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(String(execute), cx, cy + 6);
      ctx.fillStyle = css('--muted');
      ctx.font = '12px ui-monospace, Consolas, monospace';
      ctx.fillText('C2 exec', cx, cy + 25);
      ctx.textAlign = 'left';
    }
    function renderSafety(status, payloads, pairs) {
      const anySign = payloads.some(p => p.would_sign === true);
      const anyBroadcast = payloads.some(p => p.would_broadcast === true);
      const realizedNull = pairs.every(p => p.realized_net_opportunity_usd === null || p.realized_net_opportunity_usd === undefined);
      const items = [
        ['Broadcast', status.broadcast_enabled === false ? 'locked off' : text(status.broadcast_enabled), status.broadcast_enabled === false],
        ['Signing', anySign ? 'attempted' : 'locked off', !anySign],
        ['Payload Send', anyBroadcast ? 'attempted' : 'locked off', !anyBroadcast],
        ['Realized Net', realizedNull ? 'null' : 'present', realizedNull],
        ['Mode', status.realized_status || 'DRY_RUN_NO_BROADCAST', true],
        ['Logs', status.log_dir || '--', true],
      ];
      document.getElementById('safety').innerHTML = items.map(([label, value, ok]) => `
        <div class="card"><div class="label">${label}</div><div class="value-sm ${ok ? 'ok' : 'err'}">${value}</div></div>
      `).join('');
    }
    function autoIntervalMs() {
      const seconds = Math.max(15, Math.min(600, Number(document.getElementById('auto-interval').value || 60)));
      document.getElementById('auto-interval').value = seconds;
      return seconds * 1000;
    }
    async function runLiveDryRun(autoTriggered=false) {
      const button = document.getElementById('run-live');
      const limit = Math.max(1, Math.min(50, Number(document.getElementById('run-limit').value || 17)));
      if (autoRunInFlight) return;
      autoRunInFlight = true;
      button.disabled = true;
      button.textContent = autoTriggered ? 'Auto starting...' : 'Starting...';
      try {
        const result = await json(`/api/dry-run/run-live?limit=${limit}`);
        document.getElementById('runner-state').textContent = result.accepted ? 'running' : result.reason || 'not started';
        nextAutoRunAt = Date.now() + autoIntervalMs();
      } finally {
        autoRunInFlight = false;
        setTimeout(refresh, 500);
      }
    }
    function maybeAutoRun(runner) {
      const toggle = document.getElementById('auto-live');
      toggle.textContent = autoLiveEnabled ? 'Auto Live: ON' : 'Auto Live: OFF';
      toggle.className = autoLiveEnabled ? 'ok' : '';
      if (!autoLiveEnabled) {
        document.getElementById('auto-countdown').textContent = 'auto live disabled';
        return;
      }
      const remaining = Math.max(0, nextAutoRunAt - Date.now());
      document.getElementById('auto-countdown').textContent = `next live dry run in ${Math.ceil(remaining / 1000)}s`;
      if (!runner.running && remaining <= 0 && !autoRunInFlight) {
        runLiveDryRun(true);
      }
    }
    function renderRunner(runner) {
      const button = document.getElementById('run-live');
      const state = document.getElementById('runner-state');
      const running = runner.running === true;
      button.disabled = running;
      button.textContent = running ? 'Running...' : 'Run Live Dry Run';
      state.textContent = running ? `running pid ${runner.pid}` : (runner.exit_code === null || runner.exit_code === undefined ? 'idle' : `exit ${runner.exit_code}`);
      state.className = 'pill ' + (running || runner.exit_code === 0 ? 'ok' : runner.exit_code ? 'err' : '');
      document.getElementById('runner-pills').innerHTML = [
        `<span class="pill">limit ${runner.limit ?? '--'}</span>`,
        `<span class="pill">started ${runner.started_at ? new Date(runner.started_at).toLocaleTimeString() : '--'}</span>`,
        `<span class="pill">ended ${runner.ended_at ? new Date(runner.ended_at).toLocaleTimeString() : '--'}</span>`,
      ].join('');
      document.getElementById('runner-log').textContent = runner.log_tail || '';
      maybeAutoRun(runner);
    }
    async function refresh() {
      const [status, pairs, payloads, events, runner] = await Promise.all([
        json('/api/dry-run/status'),
        json('/api/dry-run/cycle-pairs?limit=50'),
        json('/api/dry-run/payloads?limit=50'),
        json('/api/dry-run/events?limit=80'),
        json('/api/dry-run/runner'),
      ]);
      document.getElementById('status-value').textContent = text(status.status).toUpperCase();
      document.getElementById('status-value').className = 'value ' + (status.status === 'completed' ? 'ok' : 'warn');
      document.getElementById('cycles').textContent = `${status.cycles_completed}/${status.limit || status.cycle_pairs}`;
      document.getElementById('run-pass').textContent = status.expected_run_pass ? 'run gate pass' : 'run gate check';
      document.getElementById('run-pass').className = 'subvalue ' + (status.expected_run_pass ? 'ok' : 'warn');
      document.getElementById('cards').textContent = status.total_dna_cards;
      document.getElementById('c2split').textContent = `${status.c2_execute}/${status.c2_no_op}`;
      document.getElementById('best-net').textContent = money(status.best_simulated_net_usd);
      document.getElementById('best-net').className = 'value ok';
      document.getElementById('total-net').textContent = money(status.total_simulated_net_usd);
      document.getElementById('total-net').className = 'value ok';
      document.getElementById('c1-gate').textContent = money(status.min_c1_profit_usd);
      document.getElementById('c2-gate').textContent = money(status.min_c2_profit_usd);
      document.getElementById('updated').textContent = new Date().toLocaleTimeString();
      document.getElementById('run-limit').value = status.limit || 17;
      renderSafety(status, payloads, pairs);
      renderRunner(runner);
      drawProfitChart(pairs);
      drawSplitChart(status);
      document.getElementById('profit-pills').innerHTML = [
        `<span class="pill">avg ${money(status.avg_simulated_net_usd)}</span>`,
        `<span class="pill">best ${money(status.best_simulated_net_usd)}</span>`,
        `<span class="pill">total ${money(status.total_simulated_net_usd)}</span>`,
      ].join('');
      document.getElementById('split-pills').innerHTML = [
        `<span class="pill ok">EXECUTE ${status.c2_execute}</span>`,
        `<span class="pill warn">NO_OP ${status.c2_no_op}</span>`,
      ].join('');
      document.getElementById('pairs').innerHTML = pairs.slice().reverse().map(p => `
        <tr>
          ${td(text(p.global_cycle_number))}
          ${td(text(p.block_number))}
          ${td(`<code>${text(p.cycle_id)}</code>`)}
          ${td(text(p.c1_decision), decisionClass(p.c1_decision))}
          ${td(text(p.c2_decision), decisionClass(p.c2_decision))}
          ${td(money4(p.simulated_c1_net_usd), Number(p.simulated_c1_net_usd) >= 0 ? 'ok' : 'err')}
          ${td(money4(p.simulated_c2_net_usd), Number(p.simulated_c2_net_usd) >= 0 ? 'ok' : 'err')}
          ${td(money4(p.simulated_net_usd), Number(p.simulated_net_usd) >= 0 ? 'ok' : 'err')}
          ${td(text(p.realized_status), 'blue')}
        </tr>
      `).join('');
      document.getElementById('payloads').innerHTML = payloads.slice().reverse().map(p => `
        <tr>
          ${td(`<code>${text(p.opportunity_id)}</code>`)}
          ${td(`<code>${text(p.cycle_id)}</code>`)}
          ${td(text(p.strike_role), 'blue')}
          ${td(text(p.would_sign), boolClass(p.would_sign === false))}
          ${td(text(p.would_broadcast), boolClass(p.would_broadcast === false))}
        </tr>
      `).join('');
      document.getElementById('events').innerHTML = events.slice().reverse().map(e => `
        <tr>
          ${td(text(e.timestamp || e.logged_at))}
          ${td(text(e.event || e.schema_type), 'blue')}
          ${td(`<code>${text(e.opportunity_id)}</code>`)}
          ${td(`<pre>${JSON.stringify(e, null, 2)}</pre>`)}
        </tr>
      `).join('');
    }
    document.getElementById('run-live').addEventListener('click', runLiveDryRun);
    document.getElementById('auto-live').addEventListener('click', () => {
      autoLiveEnabled = !autoLiveEnabled;
      nextAutoRunAt = Date.now() + autoIntervalMs();
      refresh();
    });
    document.getElementById('auto-interval').addEventListener('change', () => {
      nextAutoRunAt = Date.now() + autoIntervalMs();
      refresh();
    });
    refresh();
    setInterval(refresh, 1000);
    addEventListener('resize', () => refresh());
  </script>
</body>
</html>"""

        def log_message(self, fmt: str, *args: Any) -> None:
            print("[dry-run-dashboard]", fmt % args)

    return DryRunDashboardHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()

    log_dir = Path(args.log_dir).resolve()
    handler = make_handler(log_dir)
    server = ThreadingHTTPServer((args.host, args.port), handler)

    print("=" * 60)
    print("APEX-OMEGA DRY-RUN DASHBOARD API")
    print("=" * 60)
    print(f"URL     : http://{args.host}:{args.port}")
    print(f"Log dir : {log_dir}")
    print("Mode    : DRY RUN / NO BROADCAST")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dry-run dashboard API...")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
