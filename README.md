# Apex-Omega-v6

> **Polygon DEX flash-loan arbitrage system — Rust math core + Python orchestration + Solidity executors**

---

## ⚠️ CRITICAL SECURITY WARNING — READ FIRST

**The `.env` file committed to this repository contains live, production credentials:**

| Credential | Risk |
|---|---|
| `OWNER_PRIVATE_KEY` / `PRIVATE_KEY` | Full control of the EOA wallet `0x4Dc3f8b0C94912Edb0d74fE79B36dd8e703177f9` |
| `ALCHEMY_HTTP_1` / `ALCHEMY_WSS_1` | Alchemy Polygon mainnet API keys (rate-limited billing) |
| `INFURA_HTTP` / `INFURA_WSS` | Infura Polygon mainnet keys |
| `TELEGRAM_TOKEN` | Active Telegram bot token |
| `ONEINCH_API_KEY` | 1inch swap API key |
| `MORALIS_API_KEY` | Moralis data API JWT |
| `COINGECKO_API_KEY` | CoinGecko Pro API key |
| `POLYGONSCAN_API_KEY` | PolygonScan API key |
| `BLOXROUTE_AUTH` | bloXroute MEV auth token |
| `FIBER_AUTH` | Fiber Network auth token |

**Because these secrets are in a public Git repository, they must be considered compromised.** Anyone with access to the repo can drain the wallet, exhaust the API quotas, and access the data feeds. Rotate or revoke every credential listed above immediately.

A `.env` file should **never** be committed to version control. Add `.env` to `.gitignore` and use environment injection (CI secrets, a secrets manager, or `export VAR=value` at runtime) instead.

---

## What This System Does

Apex-Omega-v6 is a live automated arbitrage trading bot targeting the **Polygon (PoS) mainnet**. It continuously monitors price discrepancies across multiple DEXes, identifies profitable two-leg trades, funds them with flash loans, and submits signed transactions to an on-chain executor contract — all in a single atomic block.

**Target chain:** Polygon (chain ID 137)  
**Execution model:** Flash-loan arbitrage (borrow → buy cheap → sell dear → repay loan → keep profit — all in one transaction)  
**Minimum profitability gate:** `$5.00 USD` net after all costs  
**Deployment status:** Live infrastructure; `LIVE_EXECUTION=false` and `ARM_LIVE_EXECUTION=false` in the committed `.env` (shadow/dry-run mode by default)

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Repository Layout](#repository-layout)
4. [Data Pipeline — Step by Step](#data-pipeline--step-by-step)
5. [Core Modules](#core-modules)
6. [Strategy Layer (C1 / C2)](#strategy-layer-c1--c2)
7. [Rust Extension (`apex_omega_core_rust`)](#rust-extension-apex_omega_core_rust)
8. [Smart Contracts](#smart-contracts)
9. [MEV Infrastructure](#mev-infrastructure)
10. [P&L Waterfall (Full Breakdown)](#pl-waterfall-full-breakdown)
11. [Configuration Reference](#configuration-reference)
12. [Installation & Build](#installation--build)
13. [Running the System](#running-the-system)
14. [Testing](#testing)
15. [Known Limitations & Risks](#known-limitations--risks)
16. [License](#license)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  DISCOVERY LAYER (live)                                              │
│  PolygonDEXMonitor — CoinGecko / 1inch / Moralis / on-chain RPC     │
│  → up to 500 tokens × 6 DEXes × per-block pool state                │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  SCANNER SURFACE  (scanner_surface.py / dashboard_coordinator.py)   │
│  VenueQuoteRow → TokenMarketSurface → MarketExtrema                 │
│  Best-buy / best-sell selection → raw_spread / raw_spread_bps        │
│  Layer A broadcast (WebSocket scanner truth)                         │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  SLIPPAGE SENTINEL  (slippage_sentinel.py + Rust core optional)     │
│  AMM constant-product math, mempool simulation, fork validation     │
│  → predicted_slippage_bps, optimal_input, final_output, profit       │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  EXECUTION ROUTER  (execution_router.py)                             │
│  profit ≥ $100 or spread ≥ 120 bps  →  C1 AGGRESSOR (STRIKE)       │
│  otherwise                          →  C2 SURGEON  (DUPLICATE /     │
│                                        REVERSE / DO_NOTHING)        │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  MEV GAS ORACLE  (mev_gas_oracle.py)                                 │
│  eth_feeHistory → GasPriceSnapshot → TipOptimizer                   │
│  EIP-1559 maxFeePerGas / maxPriorityFeePerGas per opportunity        │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  CONTRACT INVOKER  (contract_invoker.py)                             │
│  ABI-encode calldata → eth_call simulation → optional broadcast      │
│  MEV bundle path: BundleBuilder → BundleSimulator → BundleSubmitter │
└───────────────────────────────┬──────────────────────────────────────┘
                                ↓
┌──────────────────────────────────────────────────────────────────────┐
│  ON-CHAIN EXECUTOR  (Solidity)                                       │
│  UltimateArbitrageExecutor.sol  (C1 target)                          │
│  InstitutionalExecutor.sol      (C2 target)                          │
│  Flash-loan providers: Aave V3, Balancer V2/V3                       │
│  DEX routers: UniswapV2/V3, QuickSwap, SushiSwap, Algebra            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Math / performance | **Rust** (PyO3 `cdylib`) | Optional; pure-Python fallback always available |
| Orchestration | **Python 3.8+** (`asyncio`) | Core bot logic |
| On-chain execution | **Solidity 0.8.24** | Two deployed contracts |
| Web3 interaction | **web3.py ≥ 6.0** | JSON-RPC + contract calls |
| ABI encoding | **eth-abi** | Calldata assembly |
| Data analysis | **numpy / pandas** | Feature extraction, metrics |
| External data | **aiohttp** (async HTTP) | CoinGecko, 1inch, Moralis |
| MEV relay | **Flashbots-compatible HTTP** | FastLane / Marlin / Flashbots |
| Build (Rust↔Python) | **Maturin ≥ 1.0** | `pyproject.toml` |
| Chain | **Polygon PoS** (chain ID 137) | Alchemy + Infura RPC |

---

## Repository Layout

```
Apex-Omega-v6/
├── src/
│   ├── lib.rs                  # Rust PyO3 extension — Pool, ArbitrageOpportunity,
│   │                           #   AMM math, spread, route simulation, optimisation
│   └── intake.rs               # Intake schema (Rust side)
│
├── python/
│   ├── apex_omega_core/
│   │   ├── .env                # ⚠️  LIVE CREDENTIALS — see security warning above
│   │   ├── core/
│   │   │   ├── types.py                  # All shared dataclasses (Feeds A–F, Market state…)
│   │   │   ├── spread_alignment.py       # BPS ↔ decimal conversions, raw spread
│   │   │   ├── slippage_sentinel.py      # AMM math, route simulation, mempool simulation
│   │   │   ├── inference.py              # Net-edge v7 capital model (no double-count)
│   │   │   ├── feature_factory.py        # Deterministic numeric feature extraction
│   │   │   ├── polygon_arbitrage.py      # PolygonDEXMonitor, ArbitrageDetector
│   │   │   ├── scanner_surface.py        # Venue aggregation, best-buy/sell, C1 intake
│   │   │   ├── dashboard_coordinator.py  # Async orchestration loop + WebSocket events
│   │   │   ├── contract_invoker.py       # ABI encoding, eth_call, tx broadcast, MEV bundles
│   │   │   ├── contract_targets.py       # C1/C2 contract addresses (constants)
│   │   │   ├── execution_compiler.py     # Execution compilation helpers
│   │   │   ├── mev_gas_oracle.py         # EIP-1559 fee history, P(fill) logistic, TipOptimizer
│   │   │   └── mev_bundle.py             # Flashbots-compatible bundle build/simulate/submit
│   │   │
│   │   ├── strategies/
│   │   │   ├── c1_aggressor_apex.py  # C1: full-throttle strike; Aave/Balancer flash loans
│   │   │   ├── c2_surgeon_apex.py    # C2: precision routing; DUPLICATE/REVERSE/DO_NOTHING
│   │   │   └── execution_router.py   # Routes opportunities to C1 or C2
│   │   │
│   │   ├── operations/
│   │   │   └── validate_spread_alignment.py  # Spread alignment verification
│   │   │
│   │   └── tests/
│   │       ├── conftest.py
│   │       ├── test_spread_alignment.py
│   │       ├── test_slippage_sentinel.py
│   │       ├── test_feature_factory.py
│   │       ├── test_integration.py
│   │       ├── test_scanner_surface.py
│   │       ├── test_mev_gas_oracle.py
│   │       ├── test_mev_bundle.py
│   │       ├── test_execution_compiler.py
│   │       └── test_arbitrage_detector_price_anchors.py
│   │
│   ├── dry_run.py                  # Full dry-run: live scan + metrics CSV output
│   ├── fetch_live_opportunity.py   # Stand-alone live opportunity fetcher
│   └── polygon_arbitrage_bot.py    # Main bot entry point (async scan loop)
│
├── contracts/
│   ├── UltimateArbitrageExecutor.sol   # C1 target — multi-provider flash loan executor
│   └── InstitutionalExecutor.sol       # C2 target — institutional routing executor
│
├── frontend/src/                   # Dashboard frontend (WebSocket consumer)
├── Cargo.toml                      # Rust crate config
├── pyproject.toml                  # Python package / Maturin build config
├── requirements.txt                # Python runtime dependencies
├── dry_run_results.csv             # Sample dry-run output
└── LIVE_DATA_INTEGRITY.md          # Detailed P&L waterfall with real example values
```

---

## Data Pipeline — Step by Step

### Phase 1 — Discovery

`PolygonDEXMonitor.refresh_market_registry()` (every 30 minutes, cached):
- Calls **CoinGecko** API to get the top token list for Polygon.
- Calls **1inch** API for token routing metadata.
- Calls **Moralis** API for on-chain token discovery.
- Result: up to 500 token addresses with symbols and metadata.

`PolygonDEXMonitor.scan_all_dexes()` (every scan cycle):
- For each token, queries pool reserves and prices across 6 DEXes:
  - `uniswap` (factory `0x1F98431c8...`)
  - `quickswap` (factory `0x5757371414...`)
  - `sushiswap` (factory `0xc35DADB65012...`)
  - `apeswap` (factory `0xCf083Be416...`)
  - `dfyn` (factory `0xE7Fb3e833...`)
  - `jetswap` (factory `0x668ad0ed26...`)
- Uses **Web3.py** direct on-chain calls (`slot0`, `getReserves`, `liquidity`).
- Returns a list of `Pool` objects with `mid_price_usd`, `tvl_usd`, and `fee`.

### Phase 2 — Surface Aggregation

`scanner_surface.py`:
- Groups all `Pool` objects into `TokenMarketSurface` (one per token).
- `compute_market_extrema()` selects the best buy pool (lowest ask) and best sell pool (highest bid) — only rows with `quote_confidence == "high"` are considered.
- Computes `raw_spread = best_sell_price − best_buy_price` and `raw_spread_bps`.
- Builds the **C1 intake dict** that feeds the next phase.
- Emits `scanner.token_summary` WebSocket events (Layer A dashboard).

### Phase 3 — Slippage Sentinel

`SlippageSentinel` (Python, optionally Rust-accelerated):
- **`amm_swap()`**: constant-product AMM formula `(amount_in × (1 − fee) × reserve_out) / (reserve_in + amount_in × (1 − fee))`.
- **`simulate_route()`**: runs multi-hop AMM simulation; records per-leg slippage.
- **`optimize()`**: grid-search over `[min_input, max_input]` in `steps` increments to find the `optimal_input` that maximises `profit = final_output − optimal_input − raw_spread`.
- **`mempool_validate()`**: applies `MempoolSimulator.apply_pending_tx()` to update reserves for all pending transactions affecting the same venue before re-running the sentinel simulation.
- **`validate_on_fork()`**: re-simulates the route against a shadow fork RPC endpoint to confirm the opportunity still exists.

### Phase 4 — Net Edge Derivation

`inference.py — derive_net_edge()` (APEX-OMEGA v7 capital model):
```
money_out         = buy_price  + buy_slippage
money_in          = sell_price − sell_slippage
edge              = money_in   − money_out
adjusted_slippage = ml_slippage / 3
EV_buffer         = raw_spread × buffer_rate × (trade_size / 100,000)
net_edge          = edge − adjusted_slippage − EV_buffer − fees
```

### Phase 5 — Routing Decision

`ExecutionRouter._select_strategy()`:

| Condition | Strategy |
|---|---|
| `estimated_profit_usd ≥ $100` **or** `spread_bps ≥ 120` | **C1 Aggressor** — STRIKE |
| Otherwise | **C2 Surgeon** — DUPLICATE / REVERSE / DO_NOTHING |

### Phase 6 — Gas Optimisation

`GasOracle.get_snapshot()`:
- Fetches `eth_feeHistory` for the last 20 blocks.
- Derives `base_fee_gwei`, `tip_p25/p50/p75/p90_gwei`, `gas_used_ratio_avg`.

`TipOptimizer.build_eip1559_params(p_net_usd)`:
- Logistic P(fill) model: `σ((tip − μ) / σ_slope)`.
- Grid-searches tip values to maximise `E[profit] = P(fill) × (p_net_usd − gas_cost_usd)`.
- Returns `{ maxFeePerGas, maxPriorityFeePerGas }` for the EIP-1559 transaction.

### Phase 7 — Contract Invocation

`ContractInvoker.invoke()`:
1. ABI-encodes calldata: `strike(uint256,uint256,int256)` for C1; `decide(uint8,uint256,uint256,int256)` for C2.
2. Simulates via `eth_call` — aborts if simulation reverts.
3. If `APEX_SEND_TX=1`: builds and signs a transaction (EIP-1559 by default), broadcasts via `eth_sendRawTransaction`, optionally waits for receipt.
4. Alternatively, `invoke_bundle()` wraps the transaction in a Flashbots-compatible MEV bundle (signed, simulated via `eth_callBundle`, submitted to the relay).

---

## Core Modules

### `types.py` — Shared Data Model

All inter-module communication uses typed Python dataclasses. Key types:

| Type | Purpose |
|---|---|
| `Pool` | Single DEX pool: address, dex, token0/1, TVL, fee, price |
| `ArbitrageOpportunity` | Full opportunity: buy/sell pool, prices, spread, profit, flash loan size |
| `FlashLoanConfig` | Flash loan parameters: min USD, max TVL fraction, providers |
| `VenueQuoteRow` | Raw scanner output: one entry per token × venue |
| `TokenMarketSurface` | All rows for one token grouped for surface analysis |
| `MarketExtrema` | Best-buy / best-sell selection result |
| `TokenSummaryRow` | User-facing scanner truth (dashboard Layer A) |
| `PoolState` | Per-block pool snapshot (V2 reserves or V3 slot0 + liquidity) |
| `GasState` | Per-block EIP-1559 fee snapshot |
| `MempoolState` | Sub-block pending transaction forecast |
| `RouteSnapshot` | Normalised multi-hop route object fed to C1/C2 |
| `MarketState` | Hot cache aggregating Feeds A (metadata) + B (pool state) + C (gas) |

### `spread_alignment.py` — BPS Conversions

- `bps_to_decimal(bps)` → `bps / 10000.0`
- `decimal_to_bps(decimal)` → `int(decimal * 10000)`
- `compute_raw_spread(best_sell, best_buy)` → `best_sell − best_buy`
- `compute_raw_spread_bps(best_sell, best_buy)` → `(best_sell − best_buy) / best_buy × 10,000`
- `align_spread(spread)` → round-trips bid/ask through BPS (used in validation)

### `slippage_sentinel.py` — AMM + Routing Engine

The largest and most performance-critical Python module (~1,000 lines). Key methods:

| Method | Description |
|---|---|
| `compute_raw_spread(ask_a, bid_b)` | `bid_b − ask_a`; delegates to Rust when available |
| `amm_swap(amount_in, r_in, r_out, fee)` | Constant-product AMM; delegates to Rust |
| `slippage_impact_bps(amount_in, r_in, fee_bps)` | Pre-execution linear slippage estimate |
| `depth_score(r_in, r_out, fee_bps, slippage_pct)` | Pool quality score; prunes pools below 500 |
| `simulate_route(input, route)` | Multi-hop AMM simulation; returns `(output, per_leg_slippage)` |
| `optimize(route, min_in, max_in, steps, spread)` | Grid-search for optimal trade size |
| `build_c1_slippage_context(route, spread, …)` | Full C1 sentinel context dict |
| `build_c2_slippage_context(route, spread, …)` | Full C2 sentinel context dict |
| `validate_on_fork(route, amount)` | Re-simulates against shadow fork URL |
| `mempool_validate(route, pending_txs, …)` | Mempool-adjusted simulation |
| `reverse_route(route)` | Builds the inverse two-leg route for C2 REVERSE decisions |

### `inference.py` — Net Edge Model

Implements the APEX-OMEGA v7 capital identity (no double-counting). All inputs and intermediate values are returned as named `Feature` objects for auditability.

### `polygon_arbitrage.py` — On-Chain Discovery

`PolygonDEXMonitor`:
- `refresh_market_registry()` — async token list from CoinGecko / 1inch / Moralis.
- `scan_all_dexes(tokens)` — async multi-DEX price scan; returns `List[Pool]`.
- Filters tokens by minimum TVL (`MIN_TOKEN_TVL_USD = $50,000`).

`ArbitrageDetector`:
- `find_opportunities(pools)` — cross-DEX spread comparison; returns `List[ArbitrageOpportunity]`.
- Computes flash loan sizing capped at `MAX_TVL_FRACTION` (10%) of the weaker pool.

---

## Strategy Layer (C1 / C2)

### C1 — Aggressor (`c1_aggressor_apex.py`)

Used when the opportunity is large enough to justify maximum speed.

**Flash loan provider selection** (deterministic scoring):
```
score = (spread_bps × 0.6) + (profit_usd/10 × 0.3) − (latency_ms × 0.08) − (fee_bps × 0.15)
```
Providers: Aave V3 (9 bps fee, 120 ms latency), Balancer (7 bps fee, 180 ms latency).

**Pipeline:** `prepare_contract_strike()`:
1. `build_c1_slippage_context()` — sentinel optimization.
2. `validate_on_fork()` — shadow fork re-check.
3. `mempool_validate()` — pending tx adjustment.
4. Decision: `STRIKE` if profit > 0 and mempool decision is `SAFE`; otherwise `ABORT`.

**Execution:** `execute_contract_strike()` → ABI-encode → `eth_call` → optional broadcast.

**Contract target:** `0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD` (C1_TARGET)

### C2 — Surgeon (`c2_surgeon_apex.py`)

Used for marginal opportunities; avoids unnecessary on-chain exposure.

**Trade sizing:** capped at the smaller of `flash_loan_amount` and `2% of weaker pool TVL`, with a floor of `$5,000`.

**Decision logic:**
| Condition | Action |
|---|---|
| `net_profit ≤ 0` or `total_slippage > 3%` | `DO_NOTHING` |
| Reverse route is more profitable | `REVERSE` |
| `profit > gas_cost × 2` | `DUPLICATE` |
| Otherwise | `STRIKE` |
| Mempool not `SAFE` (any decision) | Override → `DO_NOTHING` |

**Contract target:** `0x0466759822ABAA7E416276E1cf2b538d7FC540BD` (C2_TARGET)

### Execution Router (`execution_router.py`)

`process_discovery_pipeline()` runs **both** C1 and C2 plans in parallel and returns the full result dict including `eip1559_params` and `gas_cost_usd`. This is the primary entry point for the main bot loop.

---

## Rust Extension (`apex_omega_core_rust`)

Built with [Maturin](https://www.maturin.rs/). All Rust functions are optional — the Python fallback is always used when the compiled extension is unavailable.

### Exposed Functions

| Rust function | Python equivalent | Notes |
|---|---|---|
| `bps_to_decimal(bps: i32)` | `spread_alignment.bps_to_decimal` | `bps / 10000.0` |
| `decimal_to_bps(decimal: f64)` | `spread_alignment.decimal_to_bps` | `(decimal × 10000) as i32` |
| `compute_raw_spread(ask_a, bid_b)` | `SlippageSentinel.compute_raw_spread` | `bid_b − ask_a` |
| `amm_swap_core(amount_in, r_in, r_out, fee)` | `SlippageSentinel.amm_swap` | Constant-product AMM |
| `simulate_route_core(amount, r_in[], r_out[], fees[])` | `SlippageSentinel.simulate_route` | Returns `(final_out, slippages[])` |
| `optimize_route_core(min, max, steps, r_in[], r_out[], fees[])` | `SlippageSentinel.optimize` | Returns `(best_input, best_output, best_profit, slippages[])` |
| `calculate_arbitrage_profit(buy, sell, amount, fee)` | — | `(sell−buy) × amount × (1−fee)` |
| `p_fill_logistic(tip, mu, sigma)` | `PFillEstimator` | Optional Rust P(fill) acceleration |
| `optimal_tip_gwei(…)` | `TipOptimizer` | Optional Rust tip optimisation |

### Exposed Classes

| Class | Fields | Purpose |
|---|---|---|
| `Pool` | address, dex, token0, token1, tvl_usd, fee | Pool metadata |
| `ArbitrageOpportunity` | token, buy_pool, sell_pool, prices, spread_bps, profit, flash_amount, path, gas | Full opportunity |
| `FlashLoanConfig` | min_amount_usd, max_pool_tvl_percent, supported_providers | Flash loan constraints |
| `ArbitrageDetector` | dexes (8 Polygon DEXes), max_concurrent_lanes | Opportunity scanner |

**⚠️ Note on `ArbitrageDetector.find_opportunities()`:** The Rust implementation generates **mock** opportunities (hardcoded prices 1.0 / 1.005). Real opportunity detection runs entirely in the Python layer (`polygon_arbitrage.py`). The Rust `ArbitrageDetector` is a structural scaffold, not a live data source.

### Building the Rust Extension

```bash
pip install maturin
maturin develop          # debug build in-place
# or
maturin build --release  # release .whl
pip install target/wheels/apex_omega_core-*.whl
```

---

## Smart Contracts

Both contracts are on **Polygon mainnet**. Source is in `contracts/`.

### `UltimateArbitrageExecutor.sol` — C1 Target

**Address:** `0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD`

Key features:
- **Multi-provider flash loans:** Balancer V2 (`receiveFlashLoan`), Balancer V3 (unlock/settle pattern), Aave V3 (`executeOperation`), Curve.
- **Universal DEX routing:** UniswapV2-style (QuickSwap, SushiSwap), UniswapV3 (`exactInputSingle`), Algebra (QuickSwap V3).
- **Merkle proof route security:** route calldata verified against a stored Merkle root before execution.
- **Cascade slippage protection:** per-hop `minAmountOut` enforcement.
- **Emergency rescue:** owner can recover stuck tokens.

Entry point called by the bot: `strike(uint256 optimalInput, uint256 finalOutput, int256 rawSpread)`.

### `InstitutionalExecutor.sol` — C2 Target

**Address:** `0x0466759822ABAA7E416276E1cf2b538d7FC540BD`

Same flash-loan and routing infrastructure, extended with institutional controls. Entry point: `decide(uint8 decisionCode, uint256 optimalInput, uint256 finalOutput, int256 rawSpread)`.

Decision codes: `0 = DO_NOTHING`, `1 = STRIKE`, `2 = DUPLICATE`, `3 = REVERSE`.

---

## MEV Infrastructure

### Gas Oracle (`mev_gas_oracle.py`)

Polls `eth_feeHistory` for the last 20 blocks and builds a `GasPriceSnapshot`. Exposes:

- `PFillEstimator.p_fill(tip_gwei)` — logistic model: `σ((tip − μ) / σ_slope)`
- `TipOptimizer.build_eip1559_params(p_net_usd)` — grid-search for tip that maximises `E[profit] = P(fill) × (p_net − gas_cost)`

### MEV Bundle (`mev_bundle.py`)

`BundleBuilder` → `BundleSimulator` → `BundleSubmitter`:

1. Signs an EIP-1559 transaction using the operator's private key.
2. Assembles a `MEVBundle` targeting the next block.
3. Simulates via `eth_callBundle` at `APEX_SIMULATION_URL`.
4. If simulation passes, POSTs the signed bundle to `APEX_MEV_RELAY_URL` with an `X-Flashbots-Signature` header.

Configured relays (from `.env`):
- FastLane Polygon: `https://fastlane-relay.polygon.technology`
- Marlin: `https://bor.txrelay.marlin.org`
- Flashbots: same as FastLane for Polygon

### Dashboard WebSocket (`dashboard_coordinator.py`)

`DashboardCoordinator` runs an async loop at 250 ms intervals emitting:

| Event | Layer | Content |
|---|---|---|
| `scanner.venue_row` | Raw | One `VenueQuoteRow` per token × venue |
| `scanner.token_summary` | A | Best buy/sell, raw spread, scanner status |
| `c1.recompute_requested` | — | C1 is about to run for this token |
| `c1.output` | B | Optimal size, gross profit, min-outs from C1 |

---

## P&L Waterfall (Full Breakdown)

The following is an annotated example using real AAVE prices. See also `LIVE_DATA_INTEGRITY.md`.

```
════════════════════════════════════════════════════════════════
INPUT
════════════════════════════════════════════════════════════════
  Token:             AAVE
  Buy pool:          Uniswap V3         buy_price  = $250.45
  Sell pool:         QuickSwap          sell_price = $254.32
  Flash loan USD:    $50,000
  Buy pool fee:      0.3% (30 bps)
  Sell pool fee:     0.25% (25 bps)
  Flash loan fee:    9 bps  (Aave V3)
  Gas estimate:      $0.38

════════════════════════════════════════════════════════════════
1. RAW SPREAD
════════════════════════════════════════════════════════════════
  raw_spread     = sell_price − buy_price
               = $254.32 − $250.45 = $3.87 / token
  raw_spread_bps = (3.87 / 250.45) × 10,000 = 154.52 bps

════════════════════════════════════════════════════════════════
2. GROSS SPREAD (USD on full loan)
════════════════════════════════════════════════════════════════
  gross_spread_usd = raw_spread × flash_loan_usd
                  = $3.87 × $50,000 = $193,500.00

════════════════════════════════════════════════════════════════
3. DEDUCTIONS
════════════════════════════════════════════════════════════════
  Flash loan fee:   $50,000 × (9 / 10,000)     =    $45.00
  Buy pool fee:     $50,000 × 0.003             =   $150.00
  Sell pool fee:    $50,000 × 0.0025            =   $125.00
  Gas:                                          =     $0.38
  MEV buffer:                                   =     $0.15
                                               ─────────────
  Total deductions:                             =   $320.53

════════════════════════════════════════════════════════════════
4. NET PROFIT
════════════════════════════════════════════════════════════════
  Net profit = $193,500.00 − $320.53 = $193,179.47 (99.83%)

  Profitability gate ($5.00 minimum):  ✅ EXECUTABLE
════════════════════════════════════════════════════════════════
```

All deductions are auditable. Flash-loan fee bps is set by `FLASH_FEE_BPS` in `.env`. Gas is derived live from the gas oracle (fallback: `C1_GAS_USD=0.38` in `.env`).

---

## Configuration Reference

All runtime configuration is loaded from `python/apex_omega_core/.env`. Key variables:

### Network

| Variable | Example | Purpose |
|---|---|---|
| `POLYGON_RPC` | Alchemy HTTP URL | Primary data RPC |
| `POLYGON_WSS` | Alchemy WS URL | Real-time mempool stream |
| `CHAIN_ID` | `137` | Polygon mainnet |
| `APEX_RPC_URL` | (falls back to `POLYGON_RPC`) | ContractInvoker RPC |

### Credentials

| Variable | Purpose |
|---|---|
| `OWNER_PRIVATE_KEY` / `PRIVATE_KEY` | EOA signing key — **ROTATE IMMEDIATELY** |
| `APEX_PRIVATE_KEY` | Used by `ContractInvoker` and `BundleBuilder` |
| `ONEINCH_API_KEY` | 1inch swap quotes |
| `COINGECKO_API_KEY` | CoinGecko Pro token list |
| `MORALIS_API_KEY` | Moralis on-chain token discovery |
| `POLYGONSCAN_API_KEY` | PolygonScan contract ABI lookup |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Alert notifications |

### Execution Gates

| Variable | Default | Purpose |
|---|---|---|
| `LIVE_EXECUTION` | `false` | Master live-execution arm |
| `ARM_LIVE_EXECUTION` | `false` | Secondary safety arm |
| `APEX_SEND_TX` | `"0"` | `ContractInvoker` broadcast toggle (`"1"` = live) |
| `MIN_NET_PROFIT_USD` | `5.0` | Minimum USD net profit gate |
| `MIN_FLASH_LOAN_USD` | `1` | Minimum flash loan size |
| `MAX_TVL_FRACTION` | `0.10` | Max fraction of pool TVL to borrow |

### Strategy Thresholds

| Variable | Default | Purpose |
|---|---|---|
| `MIN_C1_PROFIT_USD` | `5.0` | C1 minimum profit |
| `MIN_COMBINED_PROFIT_USD` | `15.0` | Combined C1+C2 minimum |
| `MIN_GROSS_EDGE_BPS` | `5` | Minimum gross edge filter |
| `MAX_PREDICTED_SLIPPAGE_BPS` | `250` | Maximum tolerated slippage |
| `FLASH_FEE_BPS` | `9` | Aave V3 flash loan fee |
| `C1_GAS_USD` | `0.38` | C1 gas cost fallback |
| `C2_GAS_USD` | `0.55` | C2 gas cost fallback |

### MEV & Relay

| Variable | Purpose |
|---|---|
| `APEX_MEV_RELAY_URL` | MEV bundle relay endpoint |
| `APEX_FLASHBOTS_SIGNING_KEY` | Separate signing key for Flashbots header |
| `FASTLANE_RELAY` | FastLane Polygon relay URL |
| `STATIC_PRIORITY_FEE_GWEI` | `250` gwei priority fee fallback |
| `MAX_GAS_PRICE_GWEI` | `1500` gwei gas price cap |

---

## Installation & Build

### Prerequisites

- Python 3.8+
- Rust toolchain (`rustup install stable`) — *optional* for the Rust extension
- Maturin (`pip install maturin`) — *optional*, only needed to compile Rust

### Python-Only Setup

```bash
git clone https://github.com/maevnholdingstrust/Apex-Omega-v6.git
cd Apex-Omega-v6

pip install -r requirements.txt
# Optional additional dependencies used by the bot:
pip install aiohttp python-dotenv
```

### With Rust Extension (Recommended for Production)

```bash
pip install maturin
maturin develop --release   # compiles apex_omega_core_rust and installs in-place
```

Or build a distributable wheel:

```bash
maturin build --release
pip install target/wheels/apex_omega_core-*.whl
```

### Environment Setup

```bash
cp python/apex_omega_core/.env.example python/apex_omega_core/.env
# Edit .env with your own API keys, RPC endpoints, and private key
# NEVER commit .env to version control
```

> The repository currently ships a `.env` with live credentials. Those credentials must be revoked. Create your own `.env` from scratch using the variable names described in [Configuration Reference](#configuration-reference).

---

## Running the System

### Dry Run (Safe — No On-Chain Transactions)

Exercises all modules against live Polygon data, outputs metrics to `dry_run_results.csv`:

```bash
python python/dry_run.py
```

### Fetch a Live Opportunity (Read-Only)

```bash
python python/fetch_live_opportunity.py
```

### Main Bot Loop (Shadow Mode — Simulates But Does Not Broadcast)

`LIVE_EXECUTION=false` and `APEX_SEND_TX=0` must be set (they are in the committed `.env`):

```bash
python python/polygon_arbitrage_bot.py
```

### Live Execution

Only enable after:
1. Rotating all credentials in `.env`.
2. Auditing and deploying the contracts to your own addresses.
3. Funding the executor wallet.
4. Setting `LIVE_EXECUTION=true`, `ARM_LIVE_EXECUTION=true`, and `APEX_SEND_TX=1`.

```bash
python python/polygon_arbitrage_bot.py
```

---

## Testing

```bash
# All tests
pytest python/apex_omega_core/tests/ -v

# Individual suites
pytest python/apex_omega_core/tests/test_spread_alignment.py
pytest python/apex_omega_core/tests/test_slippage_sentinel.py
pytest python/apex_omega_core/tests/test_feature_factory.py
pytest python/apex_omega_core/tests/test_integration.py
pytest python/apex_omega_core/tests/test_scanner_surface.py
pytest python/apex_omega_core/tests/test_mev_gas_oracle.py
pytest python/apex_omega_core/tests/test_mev_bundle.py
pytest python/apex_omega_core/tests/test_execution_compiler.py
pytest python/apex_omega_core/tests/test_arbitrage_detector_price_anchors.py
```

---

## Known Limitations & Risks

### Security

- **Private key exposed in Git history.** Even after deleting `.env` from the repo, the key remains in Git history unless the history is rewritten. All funds associated with `0x4Dc3f8b0C94912Edb0d74fE79B36dd8e703177f9` should be considered at risk and moved immediately.
- **All API keys exposed.** Assume every key in `.env` has been seen by anyone with repo access.

### Operational

- **Rust `ArbitrageDetector.find_opportunities()` is mocked.** It generates synthetic opportunities with fixed prices (1.0 / 1.005). Live discovery runs in Python `polygon_arbitrage.py`.
- **Discovery quality depends on upstream APIs.** CoinGecko, 1inch, and Moralis outages or rate-limits degrade opportunity coverage.
- **RPC latency is the primary bottleneck.** Arbitrage windows on Polygon can close within 1–2 blocks (~2–4 seconds). High-latency RPC connections will miss most real opportunities.
- **Shadow-fork and mempool validation are best-effort.** The shadow fork URL (`SHADOW_FORK_URL`) and mempool sampling are approximate; on-chain state can change between validation and submission.
- **Slippage estimates may be inaccurate for V3 concentrated-liquidity pools.** The constant-product AMM formula used in the sentinel is exact for V2 pools but approximates V3 pools, which use tick-range liquidity.
- **`LIVE_EXECUTION=false` is the only safety net in the committed config.** A single environment variable flip enables live trading.
- **No position sizing limits beyond TVL fraction.** A single large opportunity could consume the entire bot wallet balance.
- **MEV competition.** Other bots scanning the same opportunities will front-run or sandwich unless private relay (FastLane, Flashbots) is reliably used.

### Financial

- **Flash loan fees, pool fees, and gas are real costs.** The P&L waterfall assumes fees are deducted correctly; bugs in fee accounting will lead to unprofitable trades that still execute.
- **Smart contract risk.** The executor contracts have not been publicly audited. Bugs in the Solidity could result in loss of funds.
- **This software is provided as-is, with no warranty.** Use at your own risk.

---

## License

MIT License — see repository root for full text.

---

*For a detailed walkthrough of the complete P&L waterfall with real on-chain values, see [LIVE_DATA_INTEGRITY.md](LIVE_DATA_INTEGRITY.md).*
