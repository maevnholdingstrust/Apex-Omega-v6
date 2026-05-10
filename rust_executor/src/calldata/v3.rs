use ethers::abi::{encode, Token};
use ethers::types::{Bytes, U256};
use ethers::utils::id;

use super::{CalldataGenerator, SwapCallInput};

pub struct V3Calldata;

impl CalldataGenerator for V3Calldata {
    fn build_swap_calldata(input: &SwapCallInput) -> anyhow::Result<Bytes> {
        let fee = input
            .fee
            .ok_or_else(|| anyhow::anyhow!("v3 fee required"))?;

        if fee == 0 {
            anyhow::bail!("v3 calldata rejected: fee is zero");
        }

        if input.amount_in.is_zero() || input.min_amount_out.is_zero() {
            anyhow::bail!("v3 calldata rejected: zero amount");
        }

        let selector = &id(
            "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))",
        )[0..4];

        let params = Token::Tuple(vec![
            Token::Address(input.token_in),
            Token::Address(input.token_out),
            Token::Uint(U256::from(fee)),
            Token::Address(input.recipient),
            Token::Uint(input.deadline),
            Token::Uint(input.amount_in),
            Token::Uint(input.min_amount_out),
            Token::Uint(U256::zero()),
        ]);

        let encoded = encode(&[params]);

        let mut data = selector.to_vec();
        data.extend(encoded);

        Ok(Bytes::from(data))
    }
}
