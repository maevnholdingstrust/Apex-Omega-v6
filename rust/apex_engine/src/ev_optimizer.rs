#[derive(Clone, Debug, PartialEq)]
pub struct BufferCurvePoint {
    pub multiplier: f64,
    pub buffer: f64,
    pub fill_probability: f64,
    pub profit_if_fill_usdc: f64,
    pub ev_usdc: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EvSelection {
    pub selected_buffer: f64,
    pub selected_ev_usdc: f64,
    pub curve: Vec<BufferCurvePoint>,
}

pub fn optimize_buffer_ev(
    base_buffer: f64,
    gross_profit_usdc: f64,
    gas_cost_usdc: f64,
    fill_model: impl Fn(f64) -> f64,
    slippage_model: impl Fn(f64) -> f64,
) -> EvSelection {
    let multipliers = [0.50, 0.75, 1.00, 1.25, 1.50];
    let mut curve = Vec::with_capacity(multipliers.len());
    for multiplier in multipliers {
        let buffer = base_buffer * multiplier;
        let fill_probability = fill_model(buffer).clamp(0.0, 1.0);
        let predicted_slippage = slippage_model(buffer).max(0.0);
        let profit_if_fill_usdc = gross_profit_usdc - predicted_slippage;
        let ev_usdc =
            fill_probability * profit_if_fill_usdc - (1.0 - fill_probability) * gas_cost_usdc;
        curve.push(BufferCurvePoint {
            multiplier,
            buffer,
            fill_probability,
            profit_if_fill_usdc,
            ev_usdc,
        });
    }
    let best = curve
        .iter()
        .max_by(|a, b| a.ev_usdc.total_cmp(&b.ev_usdc))
        .expect("non-empty multiplier curve");
    EvSelection {
        selected_buffer: best.buffer,
        selected_ev_usdc: best.ev_usdc,
        curve,
    }
}
