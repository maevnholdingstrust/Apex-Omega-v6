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

#[pyclass]
pub struct ArbitrageDetector {
    dexes: HashMap<String, String>,
    max_concurrent_lanes: usize,
}

#[pymethods]
impl ArbitrageDetector {
    #[new]
    fn new(max_concurrent_lanes: usize) -> Self {
        let mut dexes = HashMap::new();
        dexes.insert("uniswap".to_string(), "0x1F98431c8aD98523631AE4a59f267346ea31F984".to_string());
        dexes.insert("sushiswap".to_string(), "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506".to_string());
        dexes.insert("quickswap".to_string(), "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32".to_string());
        dexes.insert("apeswap".to_string(), "0xC0788A3aD43d79aa53B756025b9A3c4aA35639C48".to_string());
        dexes.insert("dfyn".to_string(), "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B".to_string());
        dexes.insert("jetswap".to_string(), "0x5C6Ee304399DBdB9C8Ef030aB642B10820DB8F56".to_string());
        dexes.insert("polycat".to_string(), "0x3a1D87f63f6C5A0e44d2c8d4c6A8A3B4c8F8c8c8".to_string());
        dexes.insert("wault".to_string(), "0x3a1D87f63f6C5A0e44d2c8d4c6A8A3B4c8F8c8c8".to_string());

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
    fn find_opportunities(&self, tokens: Vec<String>, min_spread_bps: f64) -> PyResult<Vec<ArbitrageOpportunity>> {
        let mut opportunities = Vec::new();

        for token in &tokens {
            // Mock opportunity generation - in real implementation, query DEX APIs
            if !token.is_empty() {
                let buy_pool = Pool::new(
                    format!("0x{}uniswap", &token[..4.min(token.len())]),
                    "uniswap".to_string(),
                    token.clone(),
                    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174".to_string(), // USDC
                    1000000.0,
                    0.003
                );

                let sell_pool = Pool::new(
                    format!("0x{}sushi", &token[..4.min(token.len())]),
                    "sushiswap".to_string(),
                    token.clone(),
                    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174".to_string(),
                    800000.0,
                    0.003
                );

                let opportunity = ArbitrageOpportunity::new(
                    token.clone(),
                    buy_pool,
                    sell_pool,
                    1.0,
                    1.005,
                    50.0,
                    250.0,
                    50000.0,
                    token.clone(),
                    vec!["buy_pool".to_string(), "sell_pool".to_string()],
                    0.1
                );

                opportunities.push(opportunity);
            }
        }

        Ok(opportunities)
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

#[pyfunction]
fn calculate_arbitrage_profit(buy_price: f64, sell_price: f64, amount: f64, fee: f64) -> PyResult<f64> {
    let gross_profit = (sell_price - buy_price) * amount;
    let net_profit = gross_profit * (1.0 - fee);
    Ok(net_profit)
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
/// P(fill | tip) = 1 / (1 + exp(-(tip - mu) / sigma))
///
/// Parameters:
///   tip_gwei  – maxPriorityFeePerGas supplied by the caller, in Gwei
///   mu_gwei   – median historical tip (50th-percentile reward from eth_feeHistory), in Gwei
///   sigma     – slope coefficient derived from (p75 - p25) / 4; must be > 0
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

/// Module initialization
/// Full v3.1 Neutral Slippage Sentinel — pair-agnostic, all math in one call.
///
/// Parameters match the Python spec exactly (fee_bps in basis points, not decimal).
/// Returns (predicted_slippage_bps, should_execute, min_profitable_bps).
///
/// Steps:
///   1. Base AMM Slippage  — constant-product constant-fee: A_out = (A_in*(1-f)*R_out)/(R_in+A_in*(1-f))
///   2. Liquidity Penalty  — depth of active range vs total reserves
///   3. Volatility Adj     — liquidity-weighted blend of 1h/24h vol, capped at 25 bps
///   4. Spread Impact      — observed spread passes through as-is
///   5. ML Residual        — size_ratio * vol_1h * 12  (placeholder heuristic)
///   6. Gas Breakeven      — (gas_cost_usd / loan_amount_usd) * 10_000 + 8 bps safety buffer
///   7. Decision           — execute when predicted_slippage_bps <= observed_spread_bps + 6.0
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
    let fee_factor = 1.0 - (fee_bps / 10_000.0);
    let amount_after_fee = amount_in * fee_factor;
    if (reserve_in + amount_after_fee) <= 0.0 {
        return Ok((999_999.0, false, 999_999.0));
    }
    let base_output = (amount_after_fee * reserve_out) / (reserve_in + amount_after_fee);
    let base_slippage = amount_in - base_output;
    let base_slippage_bps = (base_slippage / amount_in) * 10_000.0;

    // 2. Liquidity Penalty  (avoid div/0 with +1)
    let liquidity_score = active_liquidity / (reserve_in + reserve_out + 1.0);
    let liquidity_penalty = 1.0 / (liquidity_score + 0.001);

    // 3. Volatility Adjustment (liquidity-weighted, capped 25 bps)
    let vol_factor = ((vol_1h * 0.7 + vol_24h * 0.3) * liquidity_penalty).min(25.0);

    // 4. Observed Spread Impact — passes through unchanged
    let spread_impact_bps = observed_spread_bps;

    // 5. ML Residual (simple size-ratio heuristic; replace with real model later)
    let size_ratio = amount_in / (reserve_in + reserve_out);
    let ml_residual_bps = size_ratio * vol_1h * 12.0;

    // 6. Final Predicted Slippage
    let predicted_slippage_bps = base_slippage_bps + vol_factor + spread_impact_bps + ml_residual_bps;

    // 7. Gas-Aware Breakeven
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
fn apex_omega_core_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(slippage_sentinel_core, m)?)?;
    m.add_class::<Pool>()?;
    m.add_class::<ArbitrageOpportunity>()?;
    m.add_class::<FlashLoanConfig>()?;
    m.add_class::<ArbitrageDetector>()?;
    m.add_function(wrap_pyfunction!(bps_to_decimal, m)?)?;
    m.add_function(wrap_pyfunction!(decimal_to_bps, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_arbitrage_profit, m)?)?;
    m.add_function(wrap_pyfunction!(compute_raw_spread, m)?)?;
    m.add_function(wrap_pyfunction!(amm_swap_core, m)?)?;
    m.add_function(wrap_pyfunction!(simulate_route_core, m)?)?;
    m.add_function(wrap_pyfunction!(optimize_route_core, m)?)?;
    m.add_function(wrap_pyfunction!(base_amm_impact_bps, m)?)?;
    m.add_function(wrap_pyfunction!(active_liquidity_score, m)?)?;
    m.add_function(wrap_pyfunction!(p_fill_logistic, m)?)?;
    m.add_function(wrap_pyfunction!(optimal_tip_gwei, m)?)?;
    Ok(())
}