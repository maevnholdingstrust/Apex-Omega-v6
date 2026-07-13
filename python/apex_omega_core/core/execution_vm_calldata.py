"""VM calldata construction for canonical C1 flashloan payloads.

The exact VM ABI is operator-configurable through APEX_VM_C1_SIGNATURE.  The
builder defaults to a struct-compatible signature matching the canonical payload
schema in executable_payloads.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Tuple

from eth_abi import encode
from web3 import Web3

from .executable_payloads import FlashloanIntegratedC1Payload, VmStep

DEFAULT_C1_SIGNATURE = (
    "executeFlashloan(uint8,address,uint256,"
    "(address,uint256,uint256,bytes32,bytes32[],"
    "(address,address,address,uint256,uint256,uint256,bytes)[]))"
)
DEFAULT_C1_ARG_TYPES = [
    "uint8",
    "address",
    "uint256",
    "(address,uint256,uint256,bytes32,bytes32[],(address,address,address,uint256,uint256,uint256,bytes)[])",
]


@dataclass(frozen=True)
class BuiltCalldata:
    calldata: str
    calldataHash: str
    selector: str
    functionSignature: str


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _hex_to_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"expected hex string, got {value!r}")
    return bytes.fromhex(value[2:])


def _step_tuple(step: VmStep) -> Tuple[str, str, str, int, int, int, bytes]:
    step.validate()
    return (
        Web3.to_checksum_address(step.venue),
        Web3.to_checksum_address(step.tokenIn),
        Web3.to_checksum_address(step.tokenOut),
        int(step.amountIn),
        int(step.minAmountOut),
        int(step.callValue),
        _hex_to_bytes(step.payload),
    )


def build_c1_vm_calldata(payload: FlashloanIntegratedC1Payload) -> BuiltCalldata:
    """Encode a canonical C1 payload into VM calldata.

    This function does not quote, size, or invent calldata.  It only encodes a
    payload that has already passed schema and route invariant validation.
    """

    payload.assert_route_invariants()
    signature = os.getenv("APEX_VM_C1_SIGNATURE", DEFAULT_C1_SIGNATURE)
    arg_types: List[str] = DEFAULT_C1_ARG_TYPES

    ctx = payload.context
    context_tuple: Tuple[Any, ...] = (
        Web3.to_checksum_address(ctx.profitAsset),
        int(ctx.minNetProfit),
        int(ctx.nonce),
        _hex_to_bytes(ctx.merkleRoot),
        [_hex_to_bytes(p) for p in ctx.proof],
        [_step_tuple(step) for step in ctx.steps],
    )

    selector = _selector(signature)
    encoded = encode(
        arg_types,
        [
            int(payload.flashloanSource),
            Web3.to_checksum_address(payload.flashloanAsset),
            int(payload.flashloanAmount),
            context_tuple,
        ],
    )
    calldata = Web3.to_hex(selector + encoded)
    return BuiltCalldata(
        calldata=calldata,
        calldataHash=Web3.to_hex(Web3.keccak(hexstr=calldata)),
        selector=Web3.to_hex(selector),
        functionSignature=signature,
    )
