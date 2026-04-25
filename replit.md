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

- `GET /` — HTML status page with a "Run scan" button.
- `GET /healthz` — JSON health probe.
- `GET /api/modules` — JSON module load status.
- `GET /api/scan?n=20&provider=balancer&size=10000` — run a live
  Polygon scan and return JSON results.  Query params:
  `n` (1-100), `size` (USD cap), `provider`
  (`balancer` | `aave_v3` | `uniswap_v3` | `none`),
  `rpc` (override Polygon RPC URL).

## Profitability pipeline

The scan loop in `python/dry_run.py` is the system of record for
opportunity scoring.  It now:

1. **Discovers pools** on Polygon via `_discover_pools`.
2. **Derives USD prices** with `_derive_token_prices_usd`.
3. **Filters the pool universe** with `_filter_pool_universe`:
   - TVL floor: `reserve0_usd + reserve1_usd ≥ $10k`
   - Price-sanity: drop pools whose price deviates >5% from the median
     across that pair's surviving pools.  This kills stale single-tick
     UniV3 pools that previously printed 13988 bps fake spreads.
4. **Sizes each leg** with the closed-form Angeris-Chitra optimum
   (`SlippageSentinel.optimal_two_leg_input`), capped at the operator's
   max ticket size.
5. **Charges a configurable flash-loan fee**: Balancer (0 bps, default),
   Aave V3 (9 bps), UniV3 callback (0 bps), or `none` for own-capital
   execution.  Override via `FLASH_LOAN_PROVIDER` env var or
   `flash_loan_provider=` arg.

Tests for the closed-form sizing live at
`python/apex_omega_core/tests/test_optimal_two_leg_input.py`.

## Recent changes

- 2026-04-22: Initial Replit import. Installed Python 3.11 + deps, added
  `app.py` Flask dashboard, configured `Start application` workflow on
  port 5000, configured autoscale deployment with gunicorn.
- 2026-04-22: Added Angeris-Chitra closed-form optimal two-leg input
  to `SlippageSentinel`, plus 3 unit tests.
- 2026-04-22: Added `_filter_pool_universe` (TVL + price-sanity gates)
  to kill stale-pool noise; live scan dropped from ~52 fake pairs to
  ~13 real pairs with realistic sub-10-bps spreads.
- 2026-04-22: Made flash-loan provider configurable via
  `FLASH_LOAN_PROVIDER` env var; default is **Balancer (0 bps)**
  instead of Aave V3 (9 bps), which lowers break-even from ~69 bps
  to ~12 bps on UniV3 0.05% pairs.
- 2026-04-22: Added `/api/scan` JSON endpoint and a "Run scan" button
  on the dashboard.
- 2026-04-22: Parallelised `_discover_pools` with a 12-worker
  ThreadPoolExecutor (`_discover_pair` per pair).  Live Polygon scans
  dropped from **~54s/scan → ~7s/scan (~8x)** with no change to the
  RPC provider, enabling far more shots at short-lived gaps.
- 2026-04-22: **Any-route, any-pair, +$1 owner-profit seeker.**
  - Auto-generates all C(N,2) pairs from the token registry.
  - Removed the spread floor; sole filter is now
    `expected_net_edge ≥ min_net_profit_usd` (default $1.00).
  - Added `_scan_triangular_cycles`: for every (A,B,C) with all three
    legs available, picks the deepest pool per leg, runs a 24-point
    geometric size grid, and emits the best A→B→C→A cycle if it
    nets ≥ $1 after slippage + fees + 0-bps Balancer flash + 1.5×
    Polygon gas.  Both rotations searched.
  - `/api/scan` now accepts `min_profit` and `max_scans` so the UI
    returns promptly even when no qualifying route exists.
- 2026-04-25: **`c_total` renamed to `c_total_exec`; semantics locked.**
  - `c_total_exec` now strictly represents `flash_fee + gas_cost`.
  - DEX swap fees are embedded in the AMM output amounts and are
    **not** added to `c_total_exec` — they were previously double-counted.
  - `audit_two_leg_route_envelope` verifies
    `P_net_exec == P_gross_exec − c_total_exec`.
  - The interpretation `c_total_exec == flash_fee + gas_cost` is a
    semantic invariant / input contract for the pipeline, not a
    decomposition that the audit can verify from its inputs alone.
  - All references updated across `ssot_pipeline.py`, `ssot_pipeline`
    tests, and downstream consumers.
