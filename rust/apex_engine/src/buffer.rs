pub fn clamp(value: f64, min_floor: f64, max_cap: f64) -> f64 {
    value.max(min_floor).min(max_cap)
}

pub fn hybrid_buffer(
    raw_spread_decimal: f64,
    amount_usdc: f64,
    ml_predicted_slippage: f64,
    volatility_factor: f64,
    min_floor: f64,
    max_cap: f64,
) -> f64 {
    let size_scaled_buffer = 0.005 * raw_spread_decimal * (amount_usdc / 100_000.0);
    let ml_tamed_buffer = ml_predicted_slippage / 3.0;
    let hybrid_base = size_scaled_buffer.max(ml_tamed_buffer);
    clamp(hybrid_base * volatility_factor, min_floor, max_cap)
}
