# Apex Omega Final 2.0 — Product Requirements

## Original problem statement
Build a sleek modern elite GUI for Apex Omega, an institutional MEV system on Polygon 137, with the full **Discovery → Execution → Submission** pipeline.

## Architecture (locked, per user)
**Two strategies, separate pipelines, unified dashboard.**

### Strategy 1 — Arbitrage (C1 + C2 cross-DEX)
1. **Liquidity Gate (00)** — *enablement filter, NOT an execution gate.* TVL floor, price-sanity vs pair median, freshness ceiling. Failing pools are simply excluded from the route candidate universe.
2. **Discovery (01)** — USD-normalized opportunity matrix across eligible pools only. Hybrid buffer + EV optimizer.
3. **Execution (02 + 03)** —
   - **C1 Aggressor (TX 1 of 2)** — own envelope, own bundle, Block N.
   - **C2 Surgeon (TX 2 of 2)** — *only triggered by successful C1 fill.* Own envelope + Merkle proof, Block N+1, **same token/pair as C1**, action ∈ {MIRROR, REVERSE, DO_NOTHING}.
4. **Submission (04)** — 2 separate Titan bundles, independent envelopes, never merged on-chain.
5. **Archive (05)** — C1 + C2 reconciled into ONE cycle record. Each row shows C1 PnL, C2 PnL, total.

### Strategy 2 — Liquidations (Aave V3)
Scan borrower positions; HF < 1.0 → liquidatable. Flash-loan repay up to close-factor (50%), seize collateral + liquidation bonus (500–1250bps).

## Tech
- **Backend** FastAPI + Motor (MongoDB). Polygon liquidity graph + Aave positions are simulated (background ticker drifts prices every 2s). All institutional math from the spec (hybrid_buffer, EV optimizer, AMM V2/V3/Balancer/Curve, Merkle tree) implemented in `/app/backend/server.py`.
- **Frontend** React + Tailwind + framer-motion + recharts. Glassmorphism + obsidian/plasma/violet/emerald palette. Manrope + Instrument Serif + JetBrains Mono.

## What's implemented
- 2026-01: ✅ Full arbitrage pipeline E2E (Discovery → C1 → C2 (Merkle) → Submission → Archive)
- 2026-01: ✅ Liquidity Gate (TVL/price-sanity/freshness) as pre-discovery enablement
- 2026-01: ✅ Aave V3 liquidation strategy with execute + history
- 2026-01: ✅ Strategy switcher (Arbitrage / Liquidation)
- 2026-01: ✅ EV buffer curve visualisation, Risk surface panel, telemetry aggregate
- 2026-01: ✅ Clear C1=TX1/C2=TX2 visual reinforcement, "same token as C1" labelling

## Backlog (P1/P2)
- P1: Bind to live Polygon RPC (replace simulated graph) — needs RPC URL + WSS
- P1: Real Aave V3 position scanner via on-chain getUserAccountData
- P1: Live Titan relay submission (private key + bundle signing)
- P2: V3/Algebra tick-engine swap simulator (currently uses simplified active-liquidity model)
- P2: revm fallback for custom calldata routes
- P2: Builder inclusion probability ML model
- P2: Replay & RL/policy feedback from archived cycles
