use ethers::abi::{encode, Token};
use ethers::types::Bytes;
use ethers::utils::id;

use super::{CalldataGenerator, SwapCallInput};

pub struct V2Calldata;

impl CalldataGenerator for V2Calldata {
    fn build_swap_calldata(input: &SwapCallInput) -> anyhow::Result<Bytes> {
        if input.token_in == input.token_out {
            anyhow::bail!("v2 calldata rejected: token_in == token_out");
        }

        if input.amount_in.is_zero() {
            anyhow::bail!("v2 calldata rejected: amount_in is zero");
        }

        if input.min_amount_out.is_zero() {
            anyhow::bail!("v2 calldata rejected: min_amount_out is zero");
        }

        let selector =
            &id("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)")[0..4];

        let path = vec![
            Token::Address(input.token_in),
            Token::Address(input.token_out),
        ];

        let encoded = encode(&[
            Token::Uint(input.amount_in),
            Token::Uint(input.min_amount_out),
            Token::Array(path),
            Token::Address(input.recipient),
            Token::Uint(input.deadline),
        ]);

        let mut data = selector.to_vec();
        data.extend(encoded);

        Ok(Bytes::from(data))
    }
}
