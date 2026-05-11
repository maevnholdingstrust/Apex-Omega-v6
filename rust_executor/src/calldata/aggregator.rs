use ethers::types::Bytes;

use super::{CalldataGenerator, SwapCallInput};

pub struct AggregatorCalldata;

impl CalldataGenerator for AggregatorCalldata {
    fn build_swap_calldata(input: &SwapCallInput) -> anyhow::Result<Bytes> {
        let data = input
            .raw_aggregator_data
            .clone()
            .ok_or_else(|| anyhow::anyhow!("aggregator calldata missing"))?;

        if data.0.len() < 4 {
            anyhow::bail!("aggregator calldata rejected: missing selector");
        }

        Ok(data)
    }
}
