//! Intake layer — validated state assembly for the Apex-Omega arbitrage engine.
//!
//! This module defines the canonical input schema for C1 (capital model) and
//! C2 (execution decision).  Nothing downstream may compute a variable from
//! unversioned, unvalidated, or stale state; all state must pass through here
//! first.
//!
//! ## Six feeds
//!
//! | Feed | Contents                          | Change rate |
//! |------|-----------------------------------|-------------|
//! | A    | Static token / pool / router meta | Slow        |
//! | B    | Live pool state (V2 + V3)         | Per-block   |
//! | C    | Gas state                         | Per-block   |
//! | D    | Mempool / drift state             | Sub-block   |
//! | E    | Historical execution stats        | Rolling     |
//! | F    | Compiled route snapshot           | Per-cycle   |
//!
//! Feeds A–C are aggregated into [`MarketState`].  Feeds D, E, F are their own
//! top-level types.  Five audit functions enforce the intake contract before
//! any math is allowed to run.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ── Feed A: Static Metadata ──────────────────────────────────────────────────

/// Token metadata — slow-changing, cacheable on startup.
///
/// Correct decimals are critical: a mismatch here propagates a constant
/// multiplicative error through every downstream variable.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenMeta {
    /// Checksummed on-chain address.
    #[pyo3(get, set)]
    pub address: String,
    /// ERC-20 decimals (1–18).
    #[pyo3(get, set)]
    pub decimals: u8,
    /// Ticker symbol (informational).
    #[pyo3(get, set)]
    pub symbol: String,
    /// Chain ID this token lives on (e.g. 137 for Polygon).
    #[pyo3(get, set)]
    pub chain_id: u64,
}

#[pymethods]
impl TokenMeta {
    #[new]
    pub fn new(address: String, decimals: u8, symbol: String, chain_id: u64) -> Self {
        TokenMeta { address, decimals, symbol, chain_id }
    }
}

/// Router metadata — slow-changing, cacheable on startup.
///
/// The `selector_map` maps human-readable function names to their 4-byte
/// ABI selectors.  The execution builder uses this to construct calldata
/// without guessing.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouterMeta {
    /// Checksummed router contract address.
    #[pyo3(get, set)]
    pub address: String,
    /// Router variant: `"v2"`, `"v3"`, `"balancer"`, etc.
    #[pyo3(get, set)]
    pub router_type: String,
    /// DEX family label, e.g. `"uniswap"`, `"sushiswap"`, `"quickswap"`.
    #[pyo3(get, set)]
    pub dex_family: String,
    /// Function name → 4-byte hex selector, e.g. `{"swapExactTokensForTokens": "0x38ed1739"}`.
    #[pyo3(get, set)]
    pub selector_map: HashMap<String, String>,
}

#[pymethods]
impl RouterMeta {
    #[new]
    pub fn new(
        address: String,
        router_type: String,
        dex_family: String,
        selector_map: HashMap<String, String>,
    ) -> Self {
        RouterMeta { address, router_type, dex_family, selector_map }
    }
}

/// Pool metadata — slow-changing, cacheable on startup.
///
/// Provides the fee tier and router binding needed to build calldata and to
/// route-normalize the hop sequence.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PoolMeta {
    /// Checksummed pool contract address.
    #[pyo3(get, set)]
    pub address: String,
    /// Pool variant: `"v2"` or `"v3"`.
    #[pyo3(get, set)]
    pub pool_type: String,
    /// DEX family label.
    #[pyo3(get, set)]
    pub dex_family: String,
    /// Protocol fee as a decimal, e.g. `0.003` for 30 bps.
    #[pyo3(get, set)]
    pub fee_tier: f64,
    /// Checksummed address of token0.
    #[pyo3(get, set)]
    pub token0: String,
    /// Checksummed address of token1.
    #[pyo3(get, set)]
    pub token1: String,
    /// Checksummed address of the router that services this pool.
    #[pyo3(get, set)]
    pub router_address: String,
}

#[pymethods]
impl PoolMeta {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        address: String,
        pool_type: String,
        dex_family: String,
        fee_tier: f64,
        token0: String,
        token1: String,
        router_address: String,
    ) -> Self {
        PoolMeta { address, pool_type, dex_family, fee_tier, token0, token1, router_address }
    }
}

// ── Feed B: Live Pool State ───────────────────────────────────────────────────

/// Live pool state snapshot — fast-changing, keyed by `(pool_address, block_number)`.
///
/// Covers both V2-style (constant-product reserve pairs) and V3-style
/// (concentrated-liquidity sqrt-price / tick) pools.  Fields that do not apply
/// to the pool type are set to `0.0` / `0`; callers must inspect `pool_type`
/// before using arithmetic fields.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PoolState {
    /// Checksummed pool contract address.
    #[pyo3(get, set)]
    pub pool_address: String,
    /// Block number at which this snapshot was taken.
    #[pyo3(get, set)]
    pub block_number: u64,
    /// Wall-clock timestamp of the snapshot in milliseconds since Unix epoch.
    #[pyo3(get, set)]
    pub snapshot_timestamp_ms: u64,
    /// Pool variant matching [`PoolMeta::pool_type`]: `"v2"` or `"v3"`.
    #[pyo3(get, set)]
    pub pool_type: String,
    // ── V2 fields ────────────────────────────────────────────────────────────
    /// V2: reserve of token0 in token-native units.
    #[pyo3(get, set)]
    pub reserve0: f64,
    /// V2: reserve of token1 in token-native units.
    #[pyo3(get, set)]
    pub reserve1: f64,
    // ── V3 fields ────────────────────────────────────────────────────────────
    /// V3: sqrtPriceX96 stored as f64 (sufficient precision for routing math).
    #[pyo3(get, set)]
    pub sqrt_price_x96: f64,
    /// V3: current tick.
    #[pyo3(get, set)]
    pub tick: i32,
    /// V3: active liquidity in the current tick range.
    #[pyo3(get, set)]
    pub liquidity: f64,
    // ── Health flag ──────────────────────────────────────────────────────────
    /// `false` when a static-call to the pool reverted during snapshot capture.
    #[pyo3(get, set)]
    pub is_callable: bool,
}

#[pymethods]
impl PoolState {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        pool_address: String,
        block_number: u64,
        snapshot_timestamp_ms: u64,
        pool_type: String,
        reserve0: f64,
        reserve1: f64,
        sqrt_price_x96: f64,
        tick: i32,
        liquidity: f64,
        is_callable: bool,
    ) -> Self {
        PoolState {
            pool_address,
            block_number,
            snapshot_timestamp_ms,
            pool_type,
            reserve0,
            reserve1,
            sqrt_price_x96,
            tick,
            liquidity,
            is_callable,
        }
    }

    /// Returns `true` when V2 reserves are populated and positive.
    pub fn has_v2_reserves(&self) -> bool {
        self.pool_type == "v2" && self.reserve0 > 0.0 && self.reserve1 > 0.0
    }

    /// Returns `true` when V3 state fields are populated and non-zero.
    pub fn has_v3_state(&self) -> bool {
        self.pool_type == "v3" && self.sqrt_price_x96 > 0.0 && self.liquidity > 0.0
    }
}

// ── Feed C: Gas State ─────────────────────────────────────────────────────────

/// Gas snapshot — very fast-changing; must be paired with the current block.
///
/// The percentile trio (`p25`, `p50`, `p75`) is derived from
/// `eth_feeHistory` and used directly by the P(fill) logistic model.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GasState {
    /// Block number this snapshot was captured at.
    #[pyo3(get, set)]
    pub block_number: u64,
    /// Wall-clock timestamp in milliseconds since Unix epoch.
    #[pyo3(get, set)]
    pub snapshot_timestamp_ms: u64,
    /// EIP-1559 base fee in Gwei.
    #[pyo3(get, set)]
    pub base_fee_gwei: f64,
    /// 25th-percentile priority fee (tip) from recent blocks, in Gwei.
    #[pyo3(get, set)]
    pub priority_fee_p25_gwei: f64,
    /// Median (50th-percentile) priority fee, in Gwei.  Used as `mu_gwei` in
    /// the P(fill) logistic model.
    #[pyo3(get, set)]
    pub priority_fee_p50_gwei: f64,
    /// 75th-percentile priority fee, in Gwei.
    #[pyo3(get, set)]
    pub priority_fee_p75_gwei: f64,
    /// Estimated gas units per route archetype, e.g. `{"v2_2hop": 200_000}`.
    #[pyo3(get, set)]
    pub gas_estimate_by_archetype: HashMap<String, u64>,
}

#[pymethods]
impl GasState {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        block_number: u64,
        snapshot_timestamp_ms: u64,
        base_fee_gwei: f64,
        priority_fee_p25_gwei: f64,
        priority_fee_p50_gwei: f64,
        priority_fee_p75_gwei: f64,
        gas_estimate_by_archetype: HashMap<String, u64>,
    ) -> Self {
        GasState {
            block_number,
            snapshot_timestamp_ms,
            base_fee_gwei,
            priority_fee_p25_gwei,
            priority_fee_p50_gwei,
            priority_fee_p75_gwei,
            gas_estimate_by_archetype,
        }
    }

    /// Derived `sigma` for the P(fill) logistic model.
    ///
    /// `sigma = (p75 − p25) / 4`, guarded against zero.
    pub fn p_fill_sigma(&self) -> f64 {
        ((self.priority_fee_p75_gwei - self.priority_fee_p25_gwei) / 4.0).max(1e-9)
    }
}

// ── Feed D: Mempool / Drift State ─────────────────────────────────────────────

/// Mempool and reserve-drift snapshot — sub-block cadence.
///
/// The `reserve_delta_forecast` encodes the estimated fractional change in
/// each pool's reserves due to pending transactions; a value of `0.05` means
/// the model predicts a 5 % shift before the next block.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MempoolState {
    /// Wall-clock timestamp of this snapshot in milliseconds since Unix epoch.
    #[pyo3(get, set)]
    pub snapshot_timestamp_ms: u64,
    /// Number of pending swaps touching any tracked pool.
    #[pyo3(get, set)]
    pub pending_swap_count: u64,
    /// Estimated reserve delta per pool: `pool_address → signed fractional change (−1..1)`.
    #[pyo3(get, set)]
    pub reserve_delta_forecast: HashMap<String, f64>,
    /// Competing MEV-bot activity density: `0.0` = none observed, `1.0` = highly congested.
    #[pyo3(get, set)]
    pub competing_bot_density: f64,
    /// Age of this snapshot in milliseconds at the moment it was retrieved.
    #[pyo3(get, set)]
    pub freshness_age_ms: u64,
}

#[pymethods]
impl MempoolState {
    #[new]
    pub fn new(
        snapshot_timestamp_ms: u64,
        pending_swap_count: u64,
        reserve_delta_forecast: HashMap<String, f64>,
        competing_bot_density: f64,
        freshness_age_ms: u64,
    ) -> Self {
        MempoolState {
            snapshot_timestamp_ms,
            pending_swap_count,
            reserve_delta_forecast,
            competing_bot_density,
            freshness_age_ms,
        }
    }

    /// Returns the forecast reserve delta for `pool_address`, defaulting to `0.0`.
    pub fn pool_delta(&self, pool_address: &str) -> f64 {
        *self.reserve_delta_forecast.get(pool_address).unwrap_or(&0.0)
    }
}

// ── Feed E: Historical Execution Stats ────────────────────────────────────────

/// Rolling-window execution statistics used to calibrate `p_exec` and EV.
///
/// All rates are decimals in `[0.0, 1.0]`.  Error fields are mean absolute
/// error expressed in basis points.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExecutionStats {
    /// Number of observations in the rolling window.
    #[pyo3(get, set)]
    pub window_size: u64,
    /// Fraction of discovered routes that resulted in a submitted transaction.
    #[pyo3(get, set)]
    pub route_hit_rate: f64,
    /// Fraction of submitted transactions that reverted on-chain.
    #[pyo3(get, set)]
    pub revert_rate: f64,
    /// Fraction of submitted transactions included in the target block.
    #[pyo3(get, set)]
    pub inclusion_rate: f64,
    /// Mean absolute error between predicted and realized slippage, in bps.
    #[pyo3(get, set)]
    pub realized_slippage_error_bps: f64,
    /// Mean absolute error between expected and actual PnL, in bps.
    #[pyo3(get, set)]
    pub expected_vs_actual_pnl_error_bps: f64,
    /// Per-router failure rates: `router_address → rate (0..1)`.
    #[pyo3(get, set)]
    pub per_router_failure_rates: HashMap<String, f64>,
}

#[pymethods]
impl ExecutionStats {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        window_size: u64,
        route_hit_rate: f64,
        revert_rate: f64,
        inclusion_rate: f64,
        realized_slippage_error_bps: f64,
        expected_vs_actual_pnl_error_bps: f64,
        per_router_failure_rates: HashMap<String, f64>,
    ) -> Self {
        ExecutionStats {
            window_size,
            route_hit_rate,
            revert_rate,
            inclusion_rate,
            realized_slippage_error_bps,
            expected_vs_actual_pnl_error_bps,
            per_router_failure_rates,
        }
    }

    /// Calibrated `p_exec` estimate: `inclusion_rate × (1 − revert_rate)`.
    pub fn p_exec_estimate(&self) -> f64 {
        (self.inclusion_rate * (1.0 - self.revert_rate)).clamp(0.0, 1.0)
    }
}

// ── Feed F: Route Snapshot ────────────────────────────────────────────────────

/// A single hop in a multi-hop route.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouteHop {
    /// Checksummed pool contract address.
    #[pyo3(get, set)]
    pub pool_address: String,
    /// Checksummed address of the token entering this hop.
    #[pyo3(get, set)]
    pub token_in: String,
    /// Checksummed address of the token leaving this hop.
    #[pyo3(get, set)]
    pub token_out: String,
    /// Protocol fee as a decimal, e.g. `0.003`.
    #[pyo3(get, set)]
    pub fee_tier: f64,
    /// Pool variant: `"v2"` or `"v3"`.
    #[pyo3(get, set)]
    pub pool_type: String,
}

#[pymethods]
impl RouteHop {
    #[new]
    pub fn new(
        pool_address: String,
        token_in: String,
        token_out: String,
        fee_tier: f64,
        pool_type: String,
    ) -> Self {
        RouteHop { pool_address, token_in, token_out, fee_tier, pool_type }
    }
}

/// Normalized route object — the canonical input consumed by C1 and C2.
///
/// The `route_id` is a stable fingerprint of the hop sequence (e.g. a hex
/// string derived from hashing the canonical path bytes).  Any component that
/// receives a `RouteSnapshot` must verify `is_valid` before proceeding; if
/// `is_valid` is `false`, `validity_flags` contains the rejection reasons.
///
/// **Punch 2 rule**: after Punch 1 executes, call [`invalidate_route_post_punch`]
/// on this snapshot.  The old snapshot must never be re-used without a full
/// state reload and recomputation.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouteSnapshot {
    /// Stable fingerprint of the ordered hop sequence.
    #[pyo3(get, set)]
    pub route_id: String,
    /// Ordered list of hops from `input_token` to `output_token`.
    #[pyo3(get, set)]
    pub hops: Vec<RouteHop>,
    /// Checksummed address of the route's input token.
    #[pyo3(get, set)]
    pub input_token: String,
    /// Checksummed address of the route's output token.
    #[pyo3(get, set)]
    pub output_token: String,
    /// Lower bound of the size search domain, in input-token native units.
    #[pyo3(get, set)]
    pub min_input: f64,
    /// Upper bound of the size search domain, in input-token native units.
    #[pyo3(get, set)]
    pub max_input: f64,
    /// Block number at which this snapshot was evaluated.
    #[pyo3(get, set)]
    pub evaluation_block_number: u64,
    /// Wall-clock timestamp of evaluation in milliseconds since Unix epoch.
    #[pyo3(get, set)]
    pub evaluation_timestamp_ms: u64,
    /// `false` after any validation failure or post-punch invalidation.
    #[pyo3(get, set)]
    pub is_valid: bool,
    /// Rejection reasons; empty when `is_valid` is `true`.
    #[pyo3(get, set)]
    pub validity_flags: Vec<String>,
}

#[pymethods]
impl RouteSnapshot {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        route_id: String,
        hops: Vec<RouteHop>,
        input_token: String,
        output_token: String,
        min_input: f64,
        max_input: f64,
        evaluation_block_number: u64,
        evaluation_timestamp_ms: u64,
    ) -> Self {
        RouteSnapshot {
            route_id,
            hops,
            input_token,
            output_token,
            min_input,
            max_input,
            evaluation_block_number,
            evaluation_timestamp_ms,
            is_valid: true,
            validity_flags: Vec::new(),
        }
    }

    /// Number of hops in the route.
    pub fn hop_count(&self) -> usize {
        self.hops.len()
    }

    /// Fee tier (decimal) for every hop in hop order.
    pub fn fee_tiers(&self) -> Vec<f64> {
        self.hops.iter().map(|h| h.fee_tier).collect()
    }
}

// ── Aggregated Hot-Cache ──────────────────────────────────────────────────────

/// Aggregated hot-cache combining live pool state (Feed B), gas state (Feed C),
/// and static metadata (Feed A).  Keyed by `block_number` for staleness checks.
///
/// All look-up helpers scan the backing `Vec` by address; for large route sets
/// consider building a `HashMap` index on the Python side.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MarketState {
    /// Block number of the most recent pool-state snapshot.
    #[pyo3(get, set)]
    pub block_number: u64,
    /// Wall-clock timestamp of the snapshot in milliseconds since Unix epoch.
    #[pyo3(get, set)]
    pub snapshot_timestamp_ms: u64,
    /// Live pool states (Feed B).
    #[pyo3(get, set)]
    pub pool_states: Vec<PoolState>,
    /// Gas snapshot (Feed C).
    #[pyo3(get, set)]
    pub gas_state: GasState,
    /// Static token metadata (Feed A).
    #[pyo3(get, set)]
    pub token_metas: Vec<TokenMeta>,
    /// Static pool metadata (Feed A).
    #[pyo3(get, set)]
    pub pool_metas: Vec<PoolMeta>,
    /// Static router metadata (Feed A).
    #[pyo3(get, set)]
    pub router_metas: Vec<RouterMeta>,
}

#[pymethods]
impl MarketState {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        block_number: u64,
        snapshot_timestamp_ms: u64,
        pool_states: Vec<PoolState>,
        gas_state: GasState,
        token_metas: Vec<TokenMeta>,
        pool_metas: Vec<PoolMeta>,
        router_metas: Vec<RouterMeta>,
    ) -> Self {
        MarketState {
            block_number,
            snapshot_timestamp_ms,
            pool_states,
            gas_state,
            token_metas,
            pool_metas,
            router_metas,
        }
    }

    /// Look up pool state by address; returns `None` if not present.
    pub fn get_pool_state(&self, pool_address: &str) -> Option<PoolState> {
        self.pool_states.iter().find(|ps| ps.pool_address == pool_address).cloned()
    }

    /// Look up token metadata by address; returns `None` if not present.
    pub fn get_token_meta(&self, token_address: &str) -> Option<TokenMeta> {
        self.token_metas.iter().find(|tm| tm.address == token_address).cloned()
    }

    /// Look up pool metadata by address; returns `None` if not present.
    pub fn get_pool_meta(&self, pool_address: &str) -> Option<PoolMeta> {
        self.pool_metas.iter().find(|pm| pm.address == pool_address).cloned()
    }

    /// Look up router metadata by address; returns `None` if not present.
    pub fn get_router_meta(&self, router_address: &str) -> Option<RouterMeta> {
        self.router_metas.iter().find(|rm| rm.address == router_address).cloned()
    }
}

// ── Intake Validation ─────────────────────────────────────────────────────────

/// Result of a single intake audit pass.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IntakeAuditResult {
    /// Name of the audit that produced this result.
    #[pyo3(get, set)]
    pub audit_name: String,
    /// `true` when all checks in this audit passed.
    #[pyo3(get, set)]
    pub passed: bool,
    /// Human-readable reasons for failure; empty when `passed` is `true`.
    #[pyo3(get, set)]
    pub failures: Vec<String>,
}

#[pymethods]
impl IntakeAuditResult {
    #[new]
    pub fn new(audit_name: String, passed: bool, failures: Vec<String>) -> Self {
        IntakeAuditResult { audit_name, passed, failures }
    }
}

// ── Audit 1: Structural Completeness ─────────────────────────────────────────

/// Intake Audit 1 — structural completeness.
///
/// Verifies that every hop in `route` has:
/// * a matching [`PoolState`] in `market_state`
/// * a matching [`PoolMeta`] with a positive fee tier
/// * [`TokenMeta`] for both `token_in` and `token_out`
///
/// Also verifies token-chain continuity (the output token of hop *i* must
/// equal the input token of hop *i+1*) and that the route's declared
/// `input_token` / `output_token` match the first and last hops.
///
/// C1 must reject the route if this audit fails.
#[pyfunction]
pub fn validate_intake_structural(
    route: &RouteSnapshot,
    market_state: &MarketState,
) -> IntakeAuditResult {
    let mut failures: Vec<String> = Vec::new();

    if route.hops.is_empty() {
        failures.push("route has no hops".to_string());
        return IntakeAuditResult {
            audit_name: "structural_completeness".to_string(),
            passed: false,
            failures,
        };
    }

    for (i, hop) in route.hops.iter().enumerate() {
        // Pool state must exist.
        if market_state.get_pool_state(&hop.pool_address).is_none() {
            failures.push(format!(
                "hop {i}: pool state missing for {}", hop.pool_address
            ));
        }

        // Pool metadata must exist with a valid fee tier.
        match market_state.get_pool_meta(&hop.pool_address) {
            None => failures.push(format!(
                "hop {i}: pool metadata missing for {}", hop.pool_address
            )),
            Some(pm) => {
                if pm.fee_tier <= 0.0 {
                    failures.push(format!(
                        "hop {i}: invalid fee tier {} for {}", pm.fee_tier, hop.pool_address
                    ));
                }
            }
        }

        // Token metadata must exist for both tokens in each hop.
        if market_state.get_token_meta(&hop.token_in).is_none() {
            failures.push(format!(
                "hop {i}: token_in metadata missing for {}", hop.token_in
            ));
        }
        if market_state.get_token_meta(&hop.token_out).is_none() {
            failures.push(format!(
                "hop {i}: token_out metadata missing for {}", hop.token_out
            ));
        }
    }

    // Token chain must be continuous.
    for i in 1..route.hops.len() {
        if route.hops[i - 1].token_out != route.hops[i].token_in {
            failures.push(format!(
                "token chain broken between hop {} and hop {}: {} → {}",
                i - 1,
                i,
                route.hops[i - 1].token_out,
                route.hops[i].token_in
            ));
        }
    }

    // Route endpoints must match first and last hops.
    let first = &route.hops[0];
    if first.token_in != route.input_token {
        failures.push(format!(
            "route input_token {} does not match first hop token_in {}",
            route.input_token, first.token_in
        ));
    }
    let last = route.hops.last().unwrap();
    if last.token_out != route.output_token {
        failures.push(format!(
            "route output_token {} does not match last hop token_out {}",
            route.output_token, last.token_out
        ));
    }

    let passed = failures.is_empty();
    IntakeAuditResult { audit_name: "structural_completeness".to_string(), passed, failures }
}

// ── Audit 2: Freshness ────────────────────────────────────────────────────────

/// Intake Audit 2 — freshness.
///
/// Rejects state older than the supplied thresholds.  A staleness failure
/// means C1 inputs are untrustworthy; the route must be suppressed and state
/// re-fetched before re-evaluation.
///
/// Parameters:
/// * `now_ms`            — current wall-clock time in milliseconds
/// * `max_pool_age_ms`   — maximum acceptable age for pool reserves
/// * `max_gas_age_ms`    — maximum acceptable age for gas state
/// * `max_mempool_age_ms`— maximum acceptable age for mempool snapshot
#[pyfunction]
pub fn validate_intake_freshness(
    market_state: &MarketState,
    mempool_state: &MempoolState,
    now_ms: u64,
    max_pool_age_ms: u64,
    max_gas_age_ms: u64,
    max_mempool_age_ms: u64,
) -> IntakeAuditResult {
    let mut failures: Vec<String> = Vec::new();

    let pool_age = now_ms.saturating_sub(market_state.snapshot_timestamp_ms);
    if pool_age > max_pool_age_ms {
        failures.push(format!(
            "pool state is {pool_age} ms old (limit {max_pool_age_ms} ms)"
        ));
    }

    let gas_age = now_ms.saturating_sub(market_state.gas_state.snapshot_timestamp_ms);
    if gas_age > max_gas_age_ms {
        failures.push(format!(
            "gas state is {gas_age} ms old (limit {max_gas_age_ms} ms)"
        ));
    }

    let mempool_age = now_ms.saturating_sub(mempool_state.snapshot_timestamp_ms);
    if mempool_age > max_mempool_age_ms {
        failures.push(format!(
            "mempool state is {mempool_age} ms old (limit {max_mempool_age_ms} ms)"
        ));
    }

    let passed = failures.is_empty();
    IntakeAuditResult { audit_name: "freshness".to_string(), passed, failures }
}

// ── Audit 3: Mathematical Sufficiency ────────────────────────────────────────

/// Intake Audit 3 — mathematical sufficiency for C1.
///
/// Checks that `market_state` contains enough pool data to simulate every
/// hop in `route` (valid reserves for V2, valid sqrt_price + liquidity for
/// V3) and that the size search domain `[min_input, max_input]` is
/// non-degenerate.  Also verifies that token decimals are within the valid
/// range 1–18.
///
/// C1 must reject the route if this audit fails.
#[pyfunction]
pub fn validate_intake_math_sufficiency(
    route: &RouteSnapshot,
    market_state: &MarketState,
) -> IntakeAuditResult {
    let mut failures: Vec<String> = Vec::new();

    if route.min_input <= 0.0 {
        failures.push(format!("min_input must be > 0 (got {})", route.min_input));
    }
    if route.min_input >= route.max_input {
        failures.push(format!(
            "degenerate size domain: min_input {} >= max_input {}",
            route.min_input, route.max_input
        ));
    }

    for (i, hop) in route.hops.iter().enumerate() {
        match market_state.get_pool_state(&hop.pool_address) {
            None => failures.push(format!(
                "hop {i}: no pool state for {}", hop.pool_address
            )),
            Some(ps) => {
                if ps.pool_type == "v2" {
                    if ps.reserve0 <= 0.0 || ps.reserve1 <= 0.0 {
                        failures.push(format!(
                            "hop {i}: V2 pool {} has zero reserves ({}, {})",
                            hop.pool_address, ps.reserve0, ps.reserve1
                        ));
                    }
                } else if ps.pool_type == "v3" {
                    if ps.sqrt_price_x96 <= 0.0 || ps.liquidity <= 0.0 {
                        failures.push(format!(
                            "hop {i}: V3 pool {} has zero sqrt_price_x96 or liquidity",
                            hop.pool_address
                        ));
                    }
                } else {
                    failures.push(format!(
                        "hop {i}: unknown pool_type '{}' for {}",
                        ps.pool_type, hop.pool_address
                    ));
                }
            }
        }

        // Verify that decimal metadata is present and in a valid range.
        for (label, addr) in [("token_in", hop.token_in.as_str()), ("token_out", hop.token_out.as_str())] {
            match market_state.get_token_meta(addr) {
                None => failures.push(format!(
                    "hop {i}: {label} metadata absent for {addr}"
                )),
                Some(tm) => {
                    if tm.decimals == 0 || tm.decimals > 18 {
                        failures.push(format!(
                            "hop {i}: {label} '{}' has implausible decimals {}",
                            addr, tm.decimals
                        ));
                    }
                }
            }
        }
    }

    let passed = failures.is_empty();
    IntakeAuditResult { audit_name: "math_sufficiency".to_string(), passed, failures }
}

// ── Audit 4: Executable Sufficiency ──────────────────────────────────────────

/// Intake Audit 4 — executable sufficiency for C2 / envelope build.
///
/// Verifies that:
/// * a gas estimate exists for `route_archetype`
/// * `exec_stats` has at least one observation so `p_exec` can be estimated
/// * every hop's router is registered in `market_state.router_metas`
/// * the size domain is valid so `min_out` can be derived
///
/// C2 must suppress the trade if this audit fails.
#[pyfunction]
pub fn validate_intake_executable(
    route: &RouteSnapshot,
    market_state: &MarketState,
    exec_stats: &ExecutionStats,
    route_archetype: String,
) -> IntakeAuditResult {
    let mut failures: Vec<String> = Vec::new();

    // Gas estimate must exist for this archetype.
    if !market_state.gas_state.gas_estimate_by_archetype.contains_key(&route_archetype) {
        failures.push(format!(
            "no gas estimate for route archetype '{route_archetype}'"
        ));
    }

    // Execution stats must have observations for p_exec to be meaningful.
    if exec_stats.window_size == 0 {
        failures.push(
            "execution stats window is empty; p_exec cannot be estimated".to_string(),
        );
    }

    // Every hop's router must be registered.
    for (i, hop) in route.hops.iter().enumerate() {
        if let Some(pm) = market_state.get_pool_meta(&hop.pool_address) {
            if market_state.get_router_meta(&pm.router_address).is_none() {
                failures.push(format!(
                    "hop {i}: router {} not registered in router_metas",
                    pm.router_address
                ));
            }
        }
    }

    // Size domain must be valid to derive min_out.
    if route.min_input <= 0.0 || route.max_input <= route.min_input {
        failures.push("size domain is invalid; cannot derive min_out".to_string());
    }

    let passed = failures.is_empty();
    IntakeAuditResult { audit_name: "executable_sufficiency".to_string(), passed, failures }
}

// ── Scanner Surface Types ─────────────────────────────────────────────────────

/// Executable quote snapshot for a single token at a single venue/pool.
///
/// This is the atomic unit produced by the scanner (one row per token × venue)
/// and consumed by the surface aggregator before C1 intake is built.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PoolSnapshot {
    /// DEX / venue label, e.g. `"uniswap_v3"`.
    #[pyo3(get, set)]
    pub venue: String,
    /// Checksummed pool contract address.
    #[pyo3(get, set)]
    pub pool_address: String,
    /// Checksummed address of the base token being traded.
    #[pyo3(get, set)]
    pub token_address: String,
    /// Checksummed address of the quote token (e.g. USDC, WMATIC).
    #[pyo3(get, set)]
    pub quote_token_address: String,
    /// Executable buy price (quote token per base token).
    #[pyo3(get, set)]
    pub buy_price_executable: f64,
    /// Executable sell price (quote token per base token).
    #[pyo3(get, set)]
    pub sell_price_executable: f64,
    /// Pool liquidity in USD at time of snapshot.
    #[pyo3(get, set)]
    pub liquidity_usd: f64,
    /// Protocol fee in basis points (e.g. 30 = 0.30%).
    #[pyo3(get, set)]
    pub fee_bps: u16,
    /// Age of this quote in milliseconds at capture time.
    #[pyo3(get, set)]
    pub freshness_ms: u64,
    /// Quote confidence level: `"high"`, `"medium"`, or `"unknown"`.
    #[pyo3(get, set)]
    pub quote_confidence: String,
    /// On-chain block number at which this quote was captured, if known.
    #[pyo3(get, set)]
    pub block_number: Option<u64>,
    /// Source identifier, e.g. `"quoter"` or `"onchain"`.
    #[pyo3(get, set)]
    pub source: String,
}

#[pymethods]
impl PoolSnapshot {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        venue: String,
        pool_address: String,
        token_address: String,
        quote_token_address: String,
        buy_price_executable: f64,
        sell_price_executable: f64,
        liquidity_usd: f64,
        fee_bps: u16,
        freshness_ms: u64,
        quote_confidence: String,
        block_number: Option<u64>,
        source: String,
    ) -> Self {
        PoolSnapshot {
            venue,
            pool_address,
            token_address,
            quote_token_address,
            buy_price_executable,
            sell_price_executable,
            liquidity_usd,
            fee_bps,
            freshness_ms,
            quote_confidence,
            block_number,
            source,
        }
    }
}

/// Canonical C1 intake: the scanner-selected venue pair plus the size grid.
///
/// C1 must **not** trust `raw_spread` / `raw_spread_bps` as authoritative —
/// they are hints from the scanner only.  C1 re-derives all math from
/// `buy_pool` and `sell_pool` snapshots and enforces `A_in_2 = A_out_1`.
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct C1Intake {
    /// Checksummed address of the base token.
    #[pyo3(get, set)]
    pub token_address: String,
    /// ERC-20 ticker symbol of the base token.
    #[pyo3(get, set)]
    pub token_symbol: String,
    /// Best buy venue snapshot selected by the scanner surface.
    #[pyo3(get, set)]
    pub buy_pool: PoolSnapshot,
    /// Best sell venue snapshot selected by the scanner surface.
    #[pyo3(get, set)]
    pub sell_pool: PoolSnapshot,
    /// Scanner-observed raw spread in absolute price units (hint only).
    /// Defined as: best_sell_price − best_buy_price.
    #[pyo3(get, set)]
    pub raw_spread: f64,
    /// Scanner-observed raw spread in basis points (hint only).
    /// Defined as: (best_sell_price − best_buy_price) / best_buy_price * 10_000.
    #[pyo3(get, set)]
    pub raw_spread_bps: f64,
    /// Candidate notional sizes in USD to evaluate on the profit curve.
    #[pyo3(get, set)]
    pub size_grid_usd: Vec<f64>,
    /// Wall-clock timestamp of the most recent row in the surface (ms since epoch).
    #[pyo3(get, set)]
    pub observed_at_ms: u64,
    /// Best available block number from buy or sell pool snapshot.
    #[pyo3(get, set)]
    pub block_number: Option<u64>,
}

#[pymethods]
impl C1Intake {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        token_address: String,
        token_symbol: String,
        buy_pool: PoolSnapshot,
        sell_pool: PoolSnapshot,
        raw_spread: f64,
        raw_spread_bps: f64,
        size_grid_usd: Vec<f64>,
        observed_at_ms: u64,
        block_number: Option<u64>,
    ) -> Self {
        C1Intake {
            token_address,
            token_symbol,
            buy_pool,
            sell_pool,
            raw_spread,
            raw_spread_bps,
            size_grid_usd,
            observed_at_ms,
            block_number,
        }
    }
}

/// Deterministic C1 output: Master Math recompute result for a single token.
///
/// `status` is one of `"DETERMINISTIC_PROFIT"`, `"NO_PROFIT"`, or
/// `"INSUFFICIENT_DATA"`.  Dashboard Layer B renders this row alongside the
/// scanner Layer A summary row.
///
/// The locked rule `A_in_2 = A_out_1` is enforced inside C1 before this
/// struct is populated.
///
/// Dashboard Layer B exposes all four key values:
///   - `best_buy_price`    — lowest executable buy
///   - `best_sell_price`   — highest executable sell
///   - `raw_spread`        — venue gap: best_sell_price − best_buy_price
///   - `net_edge_usd`      — profit after all costs: sell_proceeds − buy_cost − fees − gas
#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct C1Output {
    /// Checksummed address of the base token.
    #[pyo3(get, set)]
    pub token_address: String,
    /// ERC-20 ticker symbol.
    #[pyo3(get, set)]
    pub token_symbol: String,
    /// Best buy venue label.
    #[pyo3(get, set)]
    pub buy_venue: String,
    /// Best sell venue label.
    #[pyo3(get, set)]
    pub sell_venue: String,
    /// Recomputed buy price from Master Math.
    #[pyo3(get, set)]
    pub best_buy_price: f64,
    /// Recomputed sell price from Master Math.
    #[pyo3(get, set)]
    pub best_sell_price: f64,
    /// Raw spread: best_sell_price − best_buy_price (venue gap, no costs).
    #[pyo3(get, set)]
    pub raw_spread: f64,
    /// Raw spread in basis points: (best_sell_price − best_buy_price) / best_buy_price * 10_000.
    #[pyo3(get, set)]
    pub raw_spread_bps: f64,
    /// Optimal notional trade size in USD from profit-curve maximisation.
    #[pyo3(get, set)]
    pub optimal_size_usd: f64,
    /// Expected base-token quantity received from the buy leg.
    #[pyo3(get, set)]
    pub expected_buy_amount_token: f64,
    /// Expected USD proceeds from the sell leg.
    #[pyo3(get, set)]
    pub expected_sell_proceeds_usd: f64,
    /// Gross profit in USD: `expected_sell_proceeds_usd − optimal_size_usd`.
    /// This is the venue-gap cashflow before fees and gas.
    #[pyo3(get, set)]
    pub gross_profit_usd: f64,
    /// Net edge in USD: sell_proceeds − buy_cost − dex_fees − gas − flash_loan_fee.
    /// This is the actual profit and determines whether execution is worth doing.
    #[pyo3(get, set)]
    pub net_edge_usd: f64,
    /// Minimum token amount out from the buy leg (slippage-adjusted).
    #[pyo3(get, set)]
    pub step1_min_out: f64,
    /// Minimum USD amount out from the sell leg (slippage-adjusted).
    #[pyo3(get, set)]
    pub step2_min_out: f64,
    /// `"DETERMINISTIC_PROFIT"`, `"NO_PROFIT"`, or `"INSUFFICIENT_DATA"`.
    #[pyo3(get, set)]
    pub status: String,
}

#[pymethods]
impl C1Output {
    #[new]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        token_address: String,
        token_symbol: String,
        buy_venue: String,
        sell_venue: String,
        best_buy_price: f64,
        best_sell_price: f64,
        raw_spread: f64,
        raw_spread_bps: f64,
        optimal_size_usd: f64,
        expected_buy_amount_token: f64,
        expected_sell_proceeds_usd: f64,
        gross_profit_usd: f64,
        net_edge_usd: f64,
        step1_min_out: f64,
        step2_min_out: f64,
        status: String,
    ) -> Self {
        C1Output {
            token_address,
            token_symbol,
            buy_venue,
            sell_venue,
            best_buy_price,
            best_sell_price,
            raw_spread,
            raw_spread_bps,
            optimal_size_usd,
            expected_buy_amount_token,
            expected_sell_proceeds_usd,
            gross_profit_usd,
            net_edge_usd,
            step1_min_out,
            step2_min_out,
            status,
        }
    }
}

// ── Post-Punch Invalidation ───────────────────────────────────────────────────

/// Mark a [`RouteSnapshot`] as invalid after Punch 1 executes.
///
/// Returns a new snapshot with `is_valid = false`.  The caller must discard
/// the old snapshot, reload all state from chain, and run a full new cycle
/// before attempting Punch 2.  Passing the old snapshot to C1 or C2 after
/// this point is a protocol violation.
#[pyfunction]
pub fn invalidate_route_post_punch(mut route: RouteSnapshot) -> RouteSnapshot {
    route.is_valid = false;
    route.validity_flags.push(
        "invalidated_post_punch1: full state reload required before new cycle".to_string(),
    );
    route
}
