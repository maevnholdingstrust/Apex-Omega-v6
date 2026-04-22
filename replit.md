# Apex-Omega-v6

High-performance arbitrage / trading library (Python core, optional Rust extension via maturin).

## Replit setup

- **Language**: Python 3.11 (installed via Replit module).
- **Entry point**: `app.py` — minimal Flask dashboard on `0.0.0.0:5000` that
  reports the health of the core modules under `python/apex_omega_core`.
- **Workflow**: `Start application` runs `python app.py` on port 5000 (webview).
- **Python deps**: numpy, pandas, pytest, web3>=6.0.0, eth-abi, requests, flask.

## Project layout

- `python/apex_omega_core/` — Core trading library (spread alignment, slippage
  sentinel, inference, feature factory, dashboard coordinator, strategies).
- `python/dry_run.py` — Dry-run script that exercises the core components.
- `src/`, `Cargo.toml` — Optional Rust extension (`apex_omega_core_rust`)
  built via maturin. Not required for the dashboard to run.
- `frontend/src/components/ScannerVenueTable.tsx` — Standalone React component
  (no build setup; mirrors `VenueQuoteRow` typing).
- `contracts/` — Solidity executor contracts.
- `app.py` — Replit entry-point Flask dashboard (added during import).

## Running

- Dashboard: workflow `Start application` (auto).
- Tests: `pytest python/apex_omega_core/tests/`.
- Dry run: `python python/dry_run.py`.

## Endpoints

- `GET /` — HTML status page.
- `GET /healthz` — JSON health probe.
- `GET /api/modules` — JSON module load status.

## Recent changes

- 2026-04-22: Initial Replit import. Installed Python 3.11 + deps, added
  `app.py` Flask dashboard, configured `Start application` workflow on
  port 5000, configured autoscale deployment with gunicorn.
