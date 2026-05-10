use ethers::abi::{encode, Token};
use ethers::types::{Bytes, U256};
use ethers::utils::id;

use super::{CalldataGenerator, SwapCallInput};

pub struct AlgebraCalldata;

impl CalldataGenerator for AlgebraCalldata {
    fn build_swap_calldata(input: &SwapCallInput) -> anyhow::Result<Bytes> {
        if input.amount_in.is_zero() || input.min_amount_out.is_zero() {
            anyhow::bail!("algebra calldata rejected: zero amount");
        }

        let selector =
            &id("exactInputSingle((address,address,address,uint256,uint256,uint256,uint160))")
                [0..4];

        let params = Token::Tuple(vec![
            Token::Address(input.token_in),
            Token::Address(input.token_out),
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
