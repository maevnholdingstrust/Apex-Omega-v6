"""Live DEX execution adapters for V2 and V3 routes.

This module builds executable VM steps from already verified live quotes.  It does
not invent prices, reserves, pools, or outputs.  Upstream discovery/quote code
must supply token-native integer amounts and slippage-protected minimum output.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Sequence

from eth_abi import encode
from web3 import Web3

from .executable_payloads import VmStep


class DexFamily(str, Enum):
    V2 = "V2"
    V3 = "V3"


@dataclass(frozen=True)
class LiveLegQuote:
    family: DexFamily
    venue: str
    token_in: str
    token_out: str
    amount_in: int
    min_amount_out: int
    receiver: str
    deadline: int
    fee: int | None = None
    sqrt_price_limit_x96: int = 0
    call_value: int = 0
    quote_amount_out: int | None = None
    pool: str | None = None

    def validate_common(self) -> None:
        _addr(self.venue)
        _addr(self.token_in)
        _addr(self.token_out)
        _addr(self.receiver)
        if int(self.amount_in) <= 0:
            raise ValueError("amount_in must be positive")
        if int(self.min_amount_out) <= 0:
            raise ValueError("min_amount_out must be positive")
        if int(self.deadline) <= 0:
            raise ValueError("deadline must be positive")
        if int(self.call_value) < 0:
            raise ValueError("call_value cannot be negative")
        if self.quote_amount_out is not None and int(self.quote_amount_out) < int(self.min_amount_out):
            raise ValueError("quote_amount_out is below min_amount_out")

    def validate_v3(self) -> None:
        if self.fee is None:
            raise ValueError("V3 leg requires fee")
        if int(self.fee) <= 0:
            raise ValueError("V3 fee must be positive")
        if int(self.sqrt_price_limit_x96) < 0:
            raise ValueError("sqrt_price_limit_x96 cannot be negative")


V2_SWAP_EXACT_TOKENS_SIG = "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"
V3_EXACT_INPUT_SINGLE_SIG = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _addr(value: str) -> str:
    if not isinstance(value, str) or not Web3.is_address(value):
        raise ValueError(f"invalid EVM address: {value!r}")
    return Web3.to_checksum_address(value)


def _encode_v2_payload(leg: LiveLegQuote) -> str:
    leg.validate_common()
    encoded = encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [
            int(leg.amount_in),
            int(leg.min_amount_out),
            [_addr(leg.token_in), _addr(leg.token_out)],
            _addr(leg.receiver),
            int(leg.deadline),
        ],
    )
    return Web3.to_hex(_selector(V2_SWAP_EXACT_TOKENS_SIG) + encoded)


def _encode_v3_payload(leg: LiveLegQuote) -> str:
    leg.validate_common()
    leg.validate_v3()
    params = (
        _addr(leg.token_in),
        _addr(leg.token_out),
        int(leg.fee or 0),
        _addr(leg.receiver),
        int(leg.deadline),
        int(leg.amount_in),
        int(leg.min_amount_out),
        int(leg.sqrt_price_limit_x96),
    )
    encoded = encode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        [params],
    )
    return Web3.to_hex(_selector(V3_EXACT_INPUT_SINGLE_SIG) + encoded)


def build_vm_step_from_live_quote(leg: LiveLegQuote) -> VmStep:
    if leg.family == DexFamily.V2:
        payload = _encode_v2_payload(leg)
    elif leg.family == DexFamily.V3:
        payload = _encode_v3_payload(leg)
    else:
        raise ValueError(f"unsupported DEX family: {leg.family!r}")

    step = VmStep(
        venue=_addr(leg.venue),
        tokenIn=_addr(leg.token_in),
        tokenOut=_addr(leg.token_out),
        amountIn=int(leg.amount_in),
        minAmountOut=int(leg.min_amount_out),
        callValue=int(leg.call_value),
        payload=payload,
    )
    step.validate()
    return step


def build_vm_steps_from_live_quotes(legs: Iterable[LiveLegQuote]) -> List[VmStep]:
    steps = [build_vm_step_from_live_quote(leg) for leg in legs]
    validate_step_chain(steps)
    return steps


def validate_step_chain(steps: Sequence[VmStep]) -> None:
    if len(steps) < 2:
        raise ValueError("execution route requires at least 2 VM steps")
    for left, right in zip(steps, steps[1:]):
        if left.tokenOut.lower() != right.tokenIn.lower():
            raise ValueError(f"broken VM step chain: {left.tokenOut} != {right.tokenIn}")


def validate_round_trip_steps(steps: Sequence[VmStep], flashloan_asset: str) -> None:
    validate_step_chain(steps)
    asset = _addr(flashloan_asset).lower()
    if steps[0].tokenIn.lower() != asset:
        raise ValueError("first step tokenIn must equal flashloan asset")
    if steps[-1].tokenOut.lower() != asset:
        raise ValueError("last step tokenOut must equal flashloan asset")
