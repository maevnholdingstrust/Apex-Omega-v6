use ethers::abi::{encode, Token};
use ethers::types::{Address, Bytes, U256};

use crate::route_step_builder::{assert_live_ready, RouteStep};

#[derive(Clone, Debug)]
pub struct RouteEnvelope {
    pub version: u8,
    pub profit_token: Address,
    pub gas_reserve_asset: U256,
    pub dex_fee_reserve_asset: U256,
    pub steps: Vec<RouteStep>,
}

impl RouteEnvelope {
    pub fn new(
        version: u8,
        profit_token: Address,
        gas_reserve_asset: U256,
        dex_fee_reserve_asset: U256,
        steps: Vec<RouteStep>,
    ) -> anyhow::Result<Self> {
        if version == 0 {
            anyhow::bail!("RouteEnvelope rejected: version is zero");
        }
        assert_live_ready(&steps)?;
        Ok(Self {
            version,
            profit_token,
            gas_reserve_asset,
            dex_fee_reserve_asset,
            steps,
        })
    }

    pub fn abi_encode_institutional(&self) -> anyhow::Result<Bytes> {
        assert_live_ready(&self.steps)?;
        let steps = self
            .steps
            .iter()
            .map(|step| {
                Token::Tuple(vec![
                    Token::Uint(U256::from(step.protocol)),
                    Token::Address(step.target),
                    Token::Address(step.approve_token),
                    Token::Address(step.output_token),
                    Token::Uint(step.call_value),
                    Token::Uint(step.min_amount_in),
                    Token::Uint(step.min_amount_out),
                    Token::Uint(U256::from(step.fee_bps)),
                    Token::Bytes(step.data.0.to_vec()),
                ])
            })
            .collect::<Vec<_>>();

        Ok(Bytes::from(encode(&[
            Token::Uint(U256::from(self.version)),
            Token::Address(self.profit_token),
            Token::Uint(self.gas_reserve_asset),
            Token::Uint(self.dex_fee_reserve_asset),
            Token::Array(steps),
        ])))
    }
}
