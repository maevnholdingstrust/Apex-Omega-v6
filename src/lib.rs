pub mod intake;

use pyo3::prelude::*;
use pyo3::pyclass;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

fn validate_route_vectors(reserve_in: &[f64], reserve_out: &[f64], fees: &[f64]) -> PyResult<()> {
    if reserve_in.len() != reserve_out.len() || reserve_in.len() != fees.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "reserve_in, reserve_out, and fees must have the same length",
        ));
    }
    Ok(())
}

fn amm_swap_internal(amount_in: f64, reserve_in: f64, reserve_out: f64, fee: f64) -> f64 {
    let amount_in_with_fee = amount_in * (1.0 - fee);
    if reserve_in <= 0.0 || reserve_out <= 0.0 || amount_in_with_fee <= 0.0 {
        return 0.0;
    }
    (amount_in_with_fee * reserve_out) / (reserve_in + amount_in_with_fee)
}

#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Pool {
    #[pyo3(get, set)]
    pub address: String,
    #[pyo3(get, set)]
    pub dex: String,
    #[pyo3(get, set)]
    pub token0: String,
    #[pyo3(get, set)]
    pub token1: String,
    #[pyo3(get, set)]
    pub tvl_usd: f64,
    #[pyo3(get, set)]
    pub fee: f64,
}

#[pymethods]
impl Pool {
    #[new]
    fn new(address: String, dex: String, token0: String, token1: String, tvl_usd: f64, fee: f64) -> Self {
        Pool { address, dex, token0, token1, tvl_usd, fee }
    }
}

#[pyclass]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ArbitrageOpportunity {
    #[pyo3(get, set)]
    pub token: String,
    #[pyo3(get, set)]
    pub buy_pool: Pool,
    #[pyo3(get, set)]
    pub sell_pool: Pool,
    #[pyo3(get, set)]
    pub buy_price: f64,
    #[pyo3(get, set)]
    pub sell_price: f64,
    #[pyo3(get, set)]
    pub spread_bps: f64,
    #[pyo3(get, set)]
    pub estimated_profit_usd: f64,
    #[pyo3(get, set)]
    pub flash_loan_amount: f64,
    #[pyo3(get, set)]
    pub flash_loan_token: String,
    #[pyo3(get, set)]
    pub path: Vec<String>,
    #[pyo3(get, set)]
    pub gas_estimate: f64,
}

#[pymethods]
impl ArbitrageOpportunity {
    #[new]
    fn new(token: String, buy_pool: Pool, sell_pool: Pool, buy_price: f64, sell_price: f64,
           spread_bps: f64, estimated_profit_usd: f64, flash_loan_amount: f64,
           flash_loan_token: String, path: Vec<String>, gas_estimate: f64) -> Self {
        ArbitrageOpportunity {
            token, buy_pool, sell_pool, buy_price, sell_price, spread_bps,
            estimated_profit_usd, flash_loan_amount, flash_loan_token, path, gas_estimate
        }
    }
}

#[pyclass]
pub struct FlashLoanConfig {
    #[pyo3(get, set)]
    pub min_amount_usd: f64,
    #[pyo3(get, set)]
    pub max_pool_tvl_percent: f64,
    #[pyo3(get, set)]
    pub supported_providers: Vec<String>,
}

#[pymethods]
impl FlashLoanConfig {
    #[new]
    fn new(min_amount_usd: f64, max_pool_tvl_percent: f64, supported_providers: Vec<String>) -> Self {
        FlashLoanConfig { min_amount_usd, max_pool_tvl_percent, supported_providers }
    }

    fn calculate_flash_loan_size(&self, buy_pool_tvl: f64, sell_pool_tvl: f64, base_amount: f64) -> PyResult<f64> {
        let min_tvl = buy_pool_tvl.min(sell_pool_tvl);
        let max_loan = min_tvl * self.max_pool_tvl_percent;
        let optimal_loan = self.min_amount_usd.max(max_loan.min(base_amount));
        Ok(optimal_loan)
    }
}

/// Polygon-correct factory addresses for DEXes supported by ArbitrageDetector.
///
/// These are the canonical Polygon mainnet factory addresses.  The Rust
/// detector and the Python `PolygonDEXMonitor` must always agree; any update
/// here must be mirrored in the Python monitor's `DEX_FACTORIES` map.
pub const POLYGON_FACTORY_UNISWAP_V3: &str = "0x1F98431c8aD98523631AE4a59f267346ea31F984";
pub const POLYGON_FACTORY_SUSHISWAP: &str = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4";
pub const POLYGON_FACTORY_QUICKSWAP: &str = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32";
pub const POLYGON_FACTORY_APESWAP: &str = "0xCf083Be4164828f00cAE704EC15a36D711491284";
pub const POLYGON_FACTORY_DFYN: &str = "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B";
pub const POLYGON_FACTORY_JETSWAP: &str = "0x668ad0ed2622b0ac445205f25ee12a7d618cfb52";

/// Return the canonical Polygon factory address map.
///
/// This function is the single source of truth for Rust-side factory addresses.
/// Tests should call this function to verify correctness rather than hard-coding
/// individual constants.
pub fn polygon_factories() -> std::collections::HashMap<&'static str, &'static str> {
    std::collections::HashMap::from([
        ("uniswap", POLYGON_FACTORY_UNISWAP_V3),
        ("sushiswap", POLYGON_FACTORY_SUSHISWAP),
        ("quickswap", POLYGON_FACTORY_QUICKSWAP),
        ("apeswap", POLYGON_FACTORY_APESWAP),
        ("dfyn", POLYGON_FACTORY_DFYN),
        ("jetswap", POLYGON_FACTORY_JETSWAP),
    ])
}

#[pyclass]
pub struct ArbitrageDetector {
    dexes: HashMap<String, String>,
    max_concurrent_lanes: usize,
}

#[pymethods]
impl ArbitrageDetector {
    #[new]
    fn new(max_concurrent_lanes: usize) -> Self {
        // Use the canonical Polygon factory addresses sourced from polygon_factories().
        let dexes: HashMap<String, String> = polygon_factories()
            .into_iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();

        ArbitrageDetector {
            dexes,
            max_concurrent_lanes,
        }
    }

    fn get_dex_count(&self) -> usize {
        self.dexes.len()
    }

    fn get_max_concurrent_lanes(&self) -> usize {
        // This represents the "32 lanes" - concurrent processing capacity
        self.max_concurrent_lanes
    }

    #[pyo3(signature = (tokens, min_spread_bps=50.0))]
    #[allow(unused_variables)]
    fn find_opportunities(&self, tokens: Vec<String>, min_spread_bps: f64) -> PyResult<Vec<ArbitrageOpportunity>> {
        // Opportunity discovery requires live on-chain reserve and price data fetched
        // by the Python scanner layer (PolygonDEXMonitor / DashboardCoordinator).
        // This Rust struct serves as the type carrier; callers must construct
        // ArbitrageOpportunity instances from live scanner output and pass them in
        // rather than relying on this method to generate them.
        //
        // `_tokens` and `_min_spread_bps` are kept in the signature to preserve
        // API stability; they are reserved for a future on-chain integration path.
        Ok(Vec::new())
    }
}

/// High-performance BPS calculations
#[pyfunction]
fn bps_to_decimal(bps: i32) -> PyResult<f64> {
    Ok(bps as f64 / 10000.0)
}

#[pyfunction]
fn decimal_to_bps(decimal: f64) -> PyResult<i32> {
    Ok((decimal * 10000.0) as i32)
}

/// Price-level two-swap arbitrage profit with per-swap fee application.
///
/// This is the price-level (no reserve depth) version of the canonical two-swap
/// arbitrage equation.  Each DEX fee is applied only to the input of its own swap:
///
///   Phase B (buy side):
///     A_eff   = amount * (1 − fee1)        — fee1 reduces Swap 1 input
///     B_out_1 = A_eff / buy_price          — price-level exchange
///
///   Phase D (sell side):
///     B_eff   = B_out_1 * (1 − fee2)       — fee2 applied to Swap 2 input (B units, NOT A units)
///     A_out_2 = B_eff * sell_price         — back to A units
///
///   P_gross = A_out_2 − amount
///
/// Note: fee1 and fee2 are applied on DIFFERENT token amounts in DIFFERENT units.
/// fee1 base = amount (A tokens); fee2 base = B_out_1 (B tokens).  These are not
/// the same quantity, not the same unit, and usually not the same USD value.
///
/// For a full AMM simulation with reserve-embedded slippage use `two_leg_arb_profit`.
#[pyfunction]
fn calculate_arbitrage_profit(buy_price: f64, sell_price: f64, amount: f64, fee1: f64, fee2: f64) -> PyResult<f64> {
    if buy_price <= 0.0 || amount <= 0.0 {
        return Ok(0.0);
    }
    let a_eff = amount * (1.0 - fee1);
    let b_out_1 = a_eff / buy_price;
    let b_eff = b_out_1 * (1.0 - fee2);
    let a_out_2 = b_eff * sell_price;
    Ok(a_out_2 - amount)
}

/// Canonical two-swap arbitrage profit using constant-product AMM math.
///
/// Implements the spec-locked two-swap form in full:
///
///   Swap 1 (buy side, A → B):
///     B_out_1 = (A_in*(1−f1)*R1_out) / (R1_in + A_in*(1−f1))
///
///   Swap 2 (sell side, B → A):
///     A_out_2 = (B_out_1*(1−f2)*R2_out) / (R2_in + B_out_1*(1−f2))
///
///   P_gross = A_out_2 − A_in
///
/// Invariants (carved in stone):
///   • Swap 1 input basis  = A_in (starting asset)
///   • Swap 2 input basis  = B_out_1 (Swap 1 output — a DIFFERENT token/amount/USD value)
///   • fee1 applies only to A_in; fee2 applies only to B_out_1
///   • AMM output already embeds slippage — do NOT subtract slippage again between swaps
///   • Profit is measured only after returning to the starting asset (A)
///
/// Returns (B_out_1, A_out_2, P_gross) so callers can inspect the mid-asset inventory.
/// Net profit = P_gross − c_gas − c_loan − c_other (computed by the caller).
#[pyfunction]
fn two_leg_arb_profit(
    a_in: f64,
    fee1: f64,
    r1_in: f64,
    r1_out: f64,
    fee2: f64,
    r2_in: f64,
    r2_out: f64,
) -> PyResult<(f64, f64, f64)> {
    let b_out_1 = amm_swap_internal(a_in, r1_in, r1_out, fee1);
    let a_out_2 = amm_swap_internal(b_out_1, r2_in, r2_out, fee2);
    let p_gross = a_out_2 - a_in;
    Ok((b_out_1, a_out_2, p_gross))
}

#[pyfunction]
fn compute_raw_spread(ask_store_a: f64, bid_store_b: f64) -> PyResult<f64> {
    Ok(bid_store_b - ask_store_a)
}

#[pyfunction]
fn amm_swap_core(amount_in: f64, reserve_in: f64, reserve_out: f64, fee: f64) -> PyResult<f64> {
    Ok(amm_swap_internal(amount_in, reserve_in, reserve_out, fee))
}

#[pyfunction]
fn simulate_route_core(
    amount_in: f64,
    reserve_in: Vec<f64>,
    reserve_out: Vec<f64>,
    fees: Vec<f64>,
) -> PyResult<(f64, Vec<f64>)> {
    validate_route_vectors(&reserve_in, &reserve_out, &fees)?;

    let mut amount = amount_in;
    let mut slippages: Vec<f64> = Vec::with_capacity(reserve_in.len());

    for i in 0..reserve_in.len() {
        let rin = reserve_in[i];
        let rout = reserve_out[i];
        let fee = fees[i];
        let expected_price = if rin > 0.0 { rout / rin } else { 0.0 };
        let out = amm_swap_internal(amount, rin, rout, fee);
        let expected_out = if expected_price > 0.0 { amount * expected_price } else { 0.0 };
        let slippage = if expected_out > 0.0 {
            (1.0 - (out / expected_out)).max(0.0)
        } else {
            1.0
        };
        slippages.push(slippage);
        amount = out;
    }

    Ok((amount, slippages))
}

#[pyfunction]
fn optimize_route_core(
    min_input: f64,
    max_input: f64,
    steps: usize,
    reserve_in: Vec<f64>,
    reserve_out: Vec<f64>,
    fees: Vec<f64>,
) -> PyResult<(f64, f64, f64, Vec<f64>)> {
    validate_route_vectors(&reserve_in, &reserve_out, &fees)?;
    let step_count = steps.max(2);

    let mut best_input = min_input;
    let mut best_output = 0.0;
    let mut best_profit = f64::NEG_INFINITY;
    let mut best_slippages: Vec<f64> = Vec::new();

    for i in 0..=step_count {
        let amount_in = min_input + (max_input - min_input) * (i as f64) / (step_count as f64);
        let (final_out, slippages) = simulate_route_core(
            amount_in,
            reserve_in.clone(),
            reserve_out.clone(),
            fees.clone(),
        )?;
        let profit = final_out - amount_in;
        if profit > best_profit {
            best_input = amount_in;
            best_output = final_out;
            best_profit = profit;
            best_slippages = slippages;
        }
    }

    Ok((best_input, best_output, best_profit, best_slippages))
}

/// Returns the effective buy price (base-token per token-A) when buying token-A with
/// `amount_base_in` units of the base token.
///
/// Formula:
///   amount_token_out = AMM_swap(amount_base_in, reserve_base, reserve_token, fee)
///   best_entry_price  = amount_base_in / amount_token_out
///
/// This is the *lowest achievable* acquisition price for the given size because the
/// AMM curve sets it; any other execution path would cost more for the same output.
/// Returns f64::INFINITY when no tokens can be acquired.
#[pyfunction]
fn best_entry_price(amount_base_in: f64, reserve_base: f64, reserve_token: f64, fee: f64) -> PyResult<f64> {
    if amount_base_in <= 0.0 {
        return Ok(f64::INFINITY);
    }
    let amount_token_out = amm_swap_internal(amount_base_in, reserve_base, reserve_token, fee);
    if amount_token_out <= 0.0 {
        return Ok(f64::INFINITY);
    }
    Ok(amount_base_in / amount_token_out)
}

/// Returns the effective sell price (base-token per token-A) when selling
/// `amount_token_in` units of token-A.
///
/// Formula:
///   amount_base_out  = AMM_swap(amount_token_in, reserve_token, reserve_base, fee)
///   best_exit_price  = amount_base_out / amount_token_in
///
/// This is the *highest realizable* exit price for the given size on this venue.
/// Returns 0.0 when no base tokens can be received.
#[pyfunction]
fn best_exit_price(amount_token_in: f64, reserve_token: f64, reserve_base: f64, fee: f64) -> PyResult<f64> {
    if amount_token_in <= 0.0 {
        return Ok(0.0);
    }
    let amount_base_out = amm_swap_internal(amount_token_in, reserve_token, reserve_base, fee);
    Ok(amount_base_out / amount_token_in)
}

/// APEX-OMEGA v7 Core Capital Model — single-call decision function.
///
/// Inputs:
///   buy_price      – best_entry_price (effective buy price, base-token per token-A)
///   buy_slippage   – adverse execution slippage on the entry leg (absolute, same unit)
///   sell_price     – best_exit_price (effective sell price, base-token per token-A)
///   sell_slippage  – adverse execution slippage on the exit leg (absolute, same unit)
///   ml_slippage    – ML-predicted residual slippage; divided by 3 before deduction
///   raw_spread     – observed raw spread (sell_price − buy_price at spot) for EV_buffer
///   buffer_rate    – EV buffer scaling factor (e.g. 0.1 for 10%)
///   trade_size     – notional trade size in USD (or base-token units)
///   fees           – total protocol / flash-loan fees (same unit as prices)
///
/// Capital identities (from spec):
///   money_out        = buy_price  + buy_slippage
///   money_in         = sell_price - sell_slippage
///   edge             = money_in   - money_out
///   adjusted_slippage = ml_slippage / 3
///   EV_buffer        = raw_spread * buffer_rate * (trade_size / 100_000)
///   net_edge         = edge - adjusted_slippage - EV_buffer - fees
///
/// Returns (money_in, money_out, edge, net_edge, should_execute)
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn compute_net_edge_v7(
    buy_price: f64,
    buy_slippage: f64,
    sell_price: f64,
    sell_slippage: f64,
    ml_slippage: f64,
    raw_spread: f64,
    buffer_rate: f64,
    trade_size: f64,
    fees: f64,
) -> PyResult<(f64, f64, f64, f64, bool)> {
    let money_out = buy_price + buy_slippage;
    let money_in = sell_price - sell_slippage;
    let edge = money_in - money_out;
    let adjusted_slippage = ml_slippage / 3.0;
    let ev_buffer = raw_spread * buffer_rate * (trade_size / 100_000.0);
    let net_edge = edge - adjusted_slippage - ev_buffer - fees;
    let should_execute = net_edge > 0.0;
    Ok((money_in, money_out, edge, net_edge, should_execute))
}

/// Base AMM price impact in basis points for a single leg.
/// FeeFactor = 1 - (fee_bps / 10_000).  fee is supplied as decimal (e.g. 0.003).
/// Formula: impact_bps = ((expected_out - actual_out) / expected_out) * 10_000
#[pyfunction]
fn base_amm_impact_bps(amount_in: f64, reserve_in: f64, reserve_out: f64, fee: f64) -> PyResult<f64> {
    if reserve_in <= 0.0 || reserve_out <= 0.0 || amount_in <= 0.0 {
        return Ok(0.0);
    }
    let expected_out = amount_in * (reserve_out / reserve_in);
    let actual_out = amm_swap_internal(amount_in, reserve_in, reserve_out, fee);
    if expected_out <= 0.0 {
        return Ok(0.0);
    }
    let impact = (expected_out - actual_out) / expected_out;
    Ok(impact.max(0.0) * 10_000.0)
}

/// ActiveLiquidityScore: liquidity in current tick range / total pool liquidity.
/// V3: pass liquidity() return value for current_liquidity and the pool's max as total_liquidity.
/// Returns decimal 0.0 – 1.0.
#[pyfunction]
fn active_liquidity_score(current_liquidity: f64, total_liquidity: f64) -> PyResult<f64> {
    if total_liquidity <= 0.0 {
        return Ok(0.0);
    }
    Ok((current_liquidity / total_liquidity).clamp(0.0, 1.0))
}

/// Logistic model for P(fill): probability a transaction is included in the next block.
///
/// P(fill | tip_gwei) = 1 / (1 + exp(-(tip_gwei - mu_gwei) / sigma))
///
/// Parameters:
///   tip_gwei  – maxPriorityFeePerGas supplied by the caller, in Gwei
///   mu_gwei   – median historical tip (50th-percentile reward from eth_feeHistory), in Gwei
///   sigma     – slope coefficient derived from (p75_gwei - p25_gwei) / 4; must be > 0
///
/// Returns P(fill) clamped to [0.0, 1.0].
#[pyfunction]
fn p_fill_logistic(tip_gwei: f64, mu_gwei: f64, sigma: f64) -> PyResult<f64> {
    let safe_sigma = sigma.max(1e-9);
    let exponent = -(tip_gwei - mu_gwei) / safe_sigma;
    if exponent > 500.0 {
        return Ok(0.0);
    }
    if exponent < -500.0 {
        return Ok(1.0);
    }
    Ok(1.0 / (1.0 + exponent.exp()))
}

/// Grid-search optimal tip (Gwei) that maximises E[profit] = P(fill) × (P_net − gas_cost).
///
/// gas_cost(tip) = gas_units × (base_fee_gwei + tip) × 1e-9 × native_price_usd
///
/// Parameters:
///   p_net_usd        – net profit in USD when the trade fills (before gas)
///   base_fee_gwei    – current block base fee in Gwei (from eth_feeHistory)
///   mu_gwei          – median tip for P(fill) logistic model
///   sigma            – slope for P(fill) logistic model
///   gas_units        – estimated gas consumption of the transaction
///   native_price_usd – price of the chain's native token in USD (e.g. MATIC)
///   max_tip_gwei     – upper bound for the tip grid (typically p90 × 3)
///   steps            – number of grid points; higher = more precision
///
/// Returns (best_tip_gwei, best_expected_profit_usd, p_fill_at_best_tip).
#[pyfunction]
fn optimal_tip_gwei(
    p_net_usd: f64,
    base_fee_gwei: f64,
    mu_gwei: f64,
    sigma: f64,
    gas_units: u64,
    native_price_usd: f64,
    max_tip_gwei: f64,
    steps: usize,
) -> PyResult<(f64, f64, f64)> {
    let safe_sigma = sigma.max(1e-9);
    let step_count = steps.max(2);

    let mut best_tip = 0.0_f64;
    let mut best_ep = f64::NEG_INFINITY;
    let mut best_pf = 0.0_f64;

    for i in 0..=step_count {
        let tip = max_tip_gwei * (i as f64) / (step_count as f64);
        let gas_cost = gas_units as f64 * (base_fee_gwei + tip) * 1e-9 * native_price_usd;
        let net = p_net_usd - gas_cost;
        if net <= 0.0 {
            continue;
        }
        let exponent = -(tip - mu_gwei) / safe_sigma;
        let pf = if exponent > 500.0 {
            0.0
        } else if exponent < -500.0 {
            1.0
        } else {
            1.0 / (1.0 + exponent.exp())
        };
        let ep = pf * net;
        if ep > best_ep {
            best_ep = ep;
            best_tip = tip;
            best_pf = pf;
        }
    }

    if best_ep == f64::NEG_INFINITY {
        best_ep = 0.0;
    }
    Ok((best_tip, best_ep, best_pf))
}

/// Full v3.1 Neutral Slippage Sentinel — pair-agnostic, all math in one call.
///
/// Parameters match the Python spec exactly (fee_bps in basis points, not decimal).
/// Returns (predicted_slippage_bps, should_execute, min_profitable_bps).
///
/// `predicted_slippage_bps` captures execution costs only (AMM impact + volatility + ML
/// residual).  The observed spread is the revenue side and must NOT be included in this
/// cost estimate — including it would make the execution condition impossible to satisfy.
///
/// Steps:
///   1. Base AMM Slippage  — (expected_out − actual_out) / expected_out × 10_000 bps,
///                           where expected_out = amount_in × (reserve_out / reserve_in)
///                           and  actual_out   = AMM output with fee applied.
///                           Using expected_out as the denominator makes the measure
///                           dimensionless and consistent with observed_spread_bps.
///   2. Liquidity Penalty  — depth of active range vs total reserves
///   3. Volatility Adj     — liquidity-weighted blend of 1h/24h vol, capped at 25 bps
///   4. ML Residual        — size_ratio * vol_1h * 12  (placeholder heuristic)
///   5. Gas Breakeven      — (gas_cost_usd / loan_amount_usd) * 10_000 + 8 bps safety buffer
///   6. Decision           — execute when observed_spread_bps > predicted_slippage_bps + min_profitable_bps
#[pyfunction]
fn slippage_sentinel_core(
    amount_in: f64,
    reserve_in: f64,
    reserve_out: f64,
    fee_bps: f64,
    active_liquidity: f64,
    vol_1h: f64,
    vol_24h: f64,
    observed_spread_bps: f64,
    gas_cost_usd: f64,
    loan_amount_usd: f64,
) -> PyResult<(f64, bool, f64)> {
    // Guard: degenerate inputs
    if amount_in <= 0.0 {
        return Ok((999_999.0, false, 999_999.0));
    }

    // 1. Base AMM Slippage
    //
    // Use `expected_out` (mid-price output, no fee/impact) as the denominator so that
    // base_slippage_bps is a dimensionless fraction of the *output* token amount.
    // This keeps the units consistent with observed_spread_bps, which is also a
    // fraction of capital (A_in) — both are bps of capital deployed.  The old formula
    // used `amount_in` (input token units) in the denominator, which is wrong whenever
    // the pool exchange rate is not 1:1 (e.g. USDC/WETH pools).
    let fee_factor = 1.0 - (fee_bps / 10_000.0);
    let amount_after_fee = amount_in * fee_factor;
    if reserve_in <= 0.0 || reserve_out <= 0.0 {
        return Ok((999_999.0, false, 999_999.0));
    }
    let expected_out = amount_in * (reserve_out / reserve_in);
    if expected_out <= 0.0 {
        return Ok((999_999.0, false, 999_999.0));
    }
    let base_output = (amount_after_fee * reserve_out) / (reserve_in + amount_after_fee);
    let base_slippage_bps = ((expected_out - base_output) / expected_out).max(0.0) * 10_000.0;

    // 2. Liquidity Penalty  (avoid div/0 with +1)
    let liquidity_score = active_liquidity / (reserve_in + reserve_out + 1.0);
    let liquidity_penalty = 1.0 / (liquidity_score + 0.001);

    // 3. Volatility Adjustment (liquidity-weighted, capped 25 bps)
    let vol_factor = ((vol_1h * 0.7 + vol_24h * 0.3) * liquidity_penalty).min(25.0);

    // 4. ML Residual (simple size-ratio heuristic; replace with real model later)
    let size_ratio = amount_in / (reserve_in + reserve_out);
    let ml_residual_bps = size_ratio * vol_1h * 12.0;

    // 5. Final Predicted Slippage — execution costs only; observed spread is revenue, not cost
    let predicted_slippage_bps = base_slippage_bps + vol_factor + ml_residual_bps;

    // 6. Gas-Aware Breakeven
    let gas_bps = if loan_amount_usd > 0.0 {
        (gas_cost_usd / loan_amount_usd) * 10_000.0
    } else {
        999_999.0
    };
    let min_profitable_bps = gas_bps + 8.0;

    // P_net × P(fill) > 0 guardrail:
    // P_net > 0 when the observed spread covers predicted slippage + gas breakeven.
    // P(fill) is implicitly > 0 when gas is committed; the 8-bps safety buffer
    // inside min_profitable_bps already prices in execution-inclusion risk.
    // The combined condition collapses to: observed_spread > predicted + min_profitable.
    let should_execute = observed_spread_bps > predicted_slippage_bps + min_profitable_bps;

    Ok((predicted_slippage_bps, should_execute, min_profitable_bps))
}

#[pymodule]
fn apex_omega_core_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(slippage_sentinel_core, m)?)?;
    m.add_class::<Pool>()?;
    m.add_class::<ArbitrageOpportunity>()?;
    m.add_class::<FlashLoanConfig>()?;
    m.add_class::<ArbitrageDetector>()?;
    m.add_function(wrap_pyfunction!(bps_to_decimal, m)?)?;
    m.add_function(wrap_pyfunction!(decimal_to_bps, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_arbitrage_profit, m)?)?;
    m.add_function(wrap_pyfunction!(two_leg_arb_profit, m)?)?;
    m.add_function(wrap_pyfunction!(compute_raw_spread, m)?)?;
    m.add_function(wrap_pyfunction!(amm_swap_core, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_route_core, m)?)?;
    m.add_function(wrap_pyfunction!(optimize_route_core, m)?)?;
    m.add_function(wrap_pyfunction!(base_amm_impact_bps, m)?)?;
    m.add_function(wrap_pyfunction!(active_liquidity_score, m)?)?;
    m.add_function(wrap_pyfunction!(p_fill_logistic, m)?)?;
    m.add_function(wrap_pyfunction!(optimal_tip_gwei, m)?)?;
    m.add_function(wrap_pyfunction!(best_entry_price, m)?)?;
    m.add_function(wrap_pyfunction!(best_exit_price, m)?)?;
    m.add_function(wrap_pyfunction!(compute_net_edge_v7, m)?)?;
    // ── Intake layer ──────────────────────────────────────────────────────────
    m.add_class::<intake::TokenMeta>()?;
    m.add_class::<intake::RouterMeta>()?;
    m.add_class::<intake::PoolMeta>()?;
    m.add_class::<intake::PoolState>()?;
    m.add_class::<intake::GasState>()?;
    m.add_class::<intake::MempoolState>()?;
    m.add_class::<intake::ExecutionStats>()?;
    m.add_class::<intake::RouteHop>()?;
    m.add_class::<intake::RouteSnapshot>()?;
    m.add_class::<intake::MarketState>()?;
    m.add_class::<intake::IntakeAuditResult>()?;
    m.add_function(wrap_pyfunction!(intake::validate_intake_structural, m)?)?;
    m.add_function(wrap_pyfunction!(intake::validate_intake_freshness, m)?)?;
    m.add_function(wrap_pyfunction!(intake::validate_intake_math_sufficiency, m)?)?;
    m.add_function(wrap_pyfunction!(intake::validate_intake_executable, m.clone())?)?;
    m.add_function(wrap_pyfunction!(intake::invalidate_route_post_punch, m.clone())?)?;
    // ── Scanner surface types ─────────────────────────────────────────────────
    m.add_class::<intake::PoolSnapshot>()?;
    m.add_class::<intake::C1Intake>()?;
    m.add_class::<intake::C1Output>()?;
    Ok(())
}

// ── Unit tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn polygon_factory_addresses_are_correct() {
        let dexes = polygon_factories();
        assert_eq!(
            dexes["sushiswap"],
            "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            "sushiswap must use Polygon factory"
        );
        assert_eq!(
            dexes["apeswap"],
            "0xCf083Be4164828f00cAE704EC15a36D711491284",
            "apeswap must use Polygon factory"
        );
        assert_eq!(
            dexes["jetswap"],
            "0x668ad0ed2622b0ac445205f25ee12a7d618cfb52",
            "jetswap must use Polygon factory"
        );
        assert_eq!(
            dexes["uniswap"],
            "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "uniswap V3 factory must be present"
        );
        assert_eq!(
            dexes["quickswap"],
            "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            "quickswap factory must be present"
        );
        assert_eq!(
            dexes["dfyn"],
            "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B",
            "dfyn must use Polygon factory"
        );
    }

    #[test]
    fn arbitrage_detector_uses_polygon_factories() {
        let detector = ArbitrageDetector::new(32);
        assert_eq!(detector.get_dex_count(), polygon_factories().len());
    }

    // ── calculate_arbitrage_profit (price-level, per-swap fees) ──────────────

    #[test]
    fn calculate_arbitrage_profit_applies_fees_to_separate_swaps() {
        // Symmetric market: buy_price == sell_price, zero spread.
        // Any non-zero fee on either leg must produce a loss (gross_profit < 0).
        let profit = calculate_arbitrage_profit(1.0, 1.0, 1000.0, 0.003, 0.003).unwrap();
        assert!(profit < 0.0, "fees on a zero-spread route must produce a loss");
    }

    #[test]
    fn calculate_arbitrage_profit_fee_basis_is_per_swap() {
        // Verify that fee1 is applied to the A input and fee2 is applied to the
        // resulting B inventory (not to the original A amount again).
        // With buy_price=1.0, sell_price=1.0, amount=1000:
        //   A_eff    = 1000 * (1 - 0.003) = 997.0
        //   B_out_1  = 997.0 / 1.0 = 997.0
        //   B_eff    = 997.0 * (1 - 0.003) = 994.009
        //   A_out_2  = 994.009 * 1.0 = 994.009
        //   gross    = 994.009 - 1000 = -5.991
        let profit = calculate_arbitrage_profit(1.0, 1.0, 1000.0, 0.003, 0.003).unwrap();
        let expected = 1000.0 * (1.0 - 0.003) * (1.0 - 0.003) - 1000.0;
        assert!((profit - expected).abs() < 1e-9,
            "price-level gross profit mismatch: got {profit}, expected {expected}");
    }

    #[test]
    fn calculate_arbitrage_profit_zero_fees_returns_full_spread() {
        // With zero fees the price-level profit equals pure spread × amount / buy_price.
        let profit = calculate_arbitrage_profit(1.0, 1.05, 1000.0, 0.0, 0.0).unwrap();
        let expected = 1000.0 / 1.0 * 1.05 - 1000.0; // 50.0
        assert!((profit - expected).abs() < 1e-9);
    }

    // ── two_leg_arb_profit (full AMM, canonical form) ────────────────────────

    #[test]
    fn two_leg_arb_profit_fee_is_per_swap_not_same_notional() {
        // Crucial invariant: fee1 is charged on A_in, fee2 is charged on B_out_1.
        // B_out_1 is a DIFFERENT amount in DIFFERENT units than A_in.
        //
        // Route: 1000 USDC → WETH (pool1) → USDC (pool2)
        // pool1: R1_in=1_000_000 USDC, R1_out=1_020_000 WETH, fee1=0.003
        // pool2: R2_in=1_020_000 WETH, R2_out=1_060_000 USDC, fee2=0.0025
        let a_in = 1_000.0;
        let (b_out_1, a_out_2, p_gross) = two_leg_arb_profit(
            a_in, 0.003, 1_000_000.0, 1_020_000.0,
            0.0025, 1_020_000.0, 1_060_000.0,
        ).unwrap();

        // Independently compute each swap to confirm per-swap fee basis.
        let b_expected = amm_swap_internal(a_in, 1_000_000.0, 1_020_000.0, 0.003);
        let a_expected = amm_swap_internal(b_expected, 1_020_000.0, 1_060_000.0, 0.0025);
        assert!((b_out_1 - b_expected).abs() < 1e-9, "Swap 1 output mismatch");
        assert!((a_out_2 - a_expected).abs() < 1e-9, "Swap 2 output mismatch");
        assert!((p_gross - (a_expected - a_in)).abs() < 1e-9, "P_gross mismatch");

        // fee1 is applied to a_in (USDC); fee2 is applied to b_out_1 (WETH).
        // b_out_1 ≠ a_in in quantity and is in different units — confirm they differ.
        assert!((b_out_1 - a_in).abs() > 1.0, "fee bases must differ (different token amounts)");
    }

    #[test]
    fn two_leg_arb_profit_zero_input_returns_zero_profit() {
        let (b_out, a_out, p_gross) = two_leg_arb_profit(
            0.0, 0.003, 1_000_000.0, 1_020_000.0,
            0.0025, 1_020_000.0, 1_060_000.0,
        ).unwrap();
        assert_eq!(b_out, 0.0);
        assert_eq!(a_out, 0.0);
        assert_eq!(p_gross, 0.0);
    }

    #[test]
    fn two_leg_arb_profit_profitable_when_spread_exceeds_fees() {
        // Deep pools, meaningful spread: the route must be profitable.
        let (_, _, p_gross) = two_leg_arb_profit(
            10_000.0, 0.003, 10_000_000.0, 10_400_000.0,
            0.0025, 10_400_000.0, 10_800_000.0,
        ).unwrap();
        assert!(p_gross > 0.0,
            "should be profitable when spread > fees; got {p_gross}");
    }

    #[test]
    fn two_leg_arb_profit_swap2_input_is_swap1_output() {
        // Core invariant: the B inventory handed to Swap 2 is exactly B_out_1,
        // not the original A_in nor any manually adjusted value.
        let a_in = 5_000.0;
        let (b_out_1, a_out_2, _) = two_leg_arb_profit(
            a_in, 0.003, 1_000_000.0, 1_020_000.0,
            0.0025, 1_020_000.0, 1_060_000.0,
        ).unwrap();
        // Recompute Swap 2 starting from b_out_1 to confirm the handoff.
        let a_recomputed = amm_swap_internal(b_out_1, 1_020_000.0, 1_060_000.0, 0.0025);
        assert!((a_out_2 - a_recomputed).abs() < 1e-9,
            "Swap 2 must consume exactly B_out_1 as its input");
    }
}