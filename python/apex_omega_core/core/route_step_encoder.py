from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from eth_abi import encode
from web3 import Web3


PROTOCOL_UNISWAP_V2 = 1
PROTOCOL_UNISWAP_V3 = 2

QUICKSWAP_V2_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"


@dataclass(frozen=True)
class EncodedStep:
    protocol: int
    target: str
    approve_token: str
    output_token: str
    call_value: int
    min_amount_in: int
    min_amount_out: int
    fee_bps: int
    data: bytes

    def as_institutional_step(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "target": Web3.to_checksum_address(self.target),
            "approveToken": Web3.to_checksum_address(self.approve_token),
            "outputToken": Web3.to_checksum_address(self.output_token),
            "callValue": self.call_value,
            "minAmountIn": self.min_amount_in,
            "minAmountOut": self.min_amount_out,
            "feeBps": self.fee_bps,
            "data": self.data,
        }

    def as_ultimate_step(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "target": Web3.to_checksum_address(self.target),
            "approveToken": Web3.to_checksum_address(self.approve_token),
            "callValue": self.call_value,
            "minAmountIn": self.min_amount_in,
            "minAmountOut": self.min_amount_out,
            "feeBps": self.fee_bps,
            "data": self.data,
        }


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def encode_v2_swap_exact_tokens_for_tokens(
    amount_in: int,
    min_amount_out: int,
    path: list[str],
    recipient: str,
    deadline: int,
) -> bytes:
    if len(path) < 2:
        raise ValueError("V2 path must have at least two tokens")
    return _selector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [int(amount_in), int(min_amount_out), [Web3.to_checksum_address(p) for p in path], Web3.to_checksum_address(recipient), int(deadline)],
    )


def encode_v3_exact_input_single(
    token_in: str,
    token_out: str,
    fee: int,
    recipient: str,
    deadline: int,
    amount_in: int,
    min_amount_out: int,
    sqrt_price_limit_x96: int = 0,
) -> bytes:
    # exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))
    return _selector("exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))") + encode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        [(
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(fee),
            Web3.to_checksum_address(recipient),
            int(deadline),
            int(amount_in),
            int(min_amount_out),
            int(sqrt_price_limit_x96),
        )],
    )


def build_quickswap_v2_step(
    token_in: str,
    token_out: str,
    amount_in: int,
    min_amount_out: int,
    recipient: str,
    deadline: int,
    fee_bps: int = 30,
) -> dict[str, Any]:
    data = encode_v2_swap_exact_tokens_for_tokens(amount_in, min_amount_out, [token_in, token_out], recipient, deadline)
    return EncodedStep(
        protocol=PROTOCOL_UNISWAP_V2,
        target=QUICKSWAP_V2_ROUTER,
        approve_token=token_in,
        output_token=token_out,
        call_value=0,
        min_amount_in=int(amount_in),
        min_amount_out=int(min_amount_out),
        fee_bps=int(fee_bps),
        data=data,
    ).as_institutional_step()


def build_uniswap_v3_step(
    token_in: str,
    token_out: str,
    amount_in: int,
    min_amount_out: int,
    recipient: str,
    deadline: int,
    fee: int = 500,
) -> dict[str, Any]:
    data = encode_v3_exact_input_single(token_in, token_out, fee, recipient, deadline, amount_in, min_amount_out)
    return EncodedStep(
        protocol=PROTOCOL_UNISWAP_V3,
        target=UNISWAP_V3_ROUTER,
        approve_token=token_in,
        output_token=token_out,
        call_value=0,
        min_amount_in=int(amount_in),
        min_amount_out=int(min_amount_out),
        fee_bps=int(fee / 100),
        data=data,
    ).as_institutional_step()


def validate_route_steps(steps: list[Mapping[str, Any]]) -> None:
    if not steps:
        raise ValueError("route requires at least one step")
    for idx, step in enumerate(steps):
        if not step.get("data"):
            raise ValueError(f"step {idx} has empty calldata")
        if int(step.get("minAmountOut", 0)) <= 0:
            raise ValueError(f"step {idx} requires positive minAmountOut")
        Web3.to_checksum_address(step["target"])
        Web3.to_checksum_address(step["approveToken"])
        Web3.to_checksum_address(step["outputToken"])
