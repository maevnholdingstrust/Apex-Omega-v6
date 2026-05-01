from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from eth_abi import encode
from web3 import Web3


PROTOCOL_UNISWAP_V2 = 1
PROTOCOL_UNISWAP_V3 = 2

QUICKSWAP_V2_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
SUSHISWAP_V2_ROUTER = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"
APESWAP_V2_ROUTER = "0xC0788A3aD43d79aa53B09c2EaCc313A787d1d607"
DFYN_V2_ROUTER = "0xA8b607Aa09B6A2641cF6F90f643E76d3f6e6Ff73"
JETSWAP_V2_ROUTER = "0x5c6eBB8ba4bFe04bdeA4eF6c6eBf3eF2cA19E3c2"
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"


@dataclass(frozen=True)
class V2RouterSpec:
    name: str
    router: str
    fee_bps: int
    supported: bool = True
    notes: str = ""


V2_ROUTER_REGISTRY: dict[str, V2RouterSpec] = {
    "quickswap": V2RouterSpec("quickswap_v2", QUICKSWAP_V2_ROUTER, 30),
    "quickswap_v2": V2RouterSpec("quickswap_v2", QUICKSWAP_V2_ROUTER, 30),
    "qsv2": V2RouterSpec("quickswap_v2", QUICKSWAP_V2_ROUTER, 30),
    "sushi": V2RouterSpec("sushiswap_v2", SUSHISWAP_V2_ROUTER, 30),
    "sushiswap": V2RouterSpec("sushiswap_v2", SUSHISWAP_V2_ROUTER, 30),
    "sushiswap_v2": V2RouterSpec("sushiswap_v2", SUSHISWAP_V2_ROUTER, 30),
    "apeswap": V2RouterSpec("apeswap_v2", APESWAP_V2_ROUTER, 30),
    "apeswap_v2": V2RouterSpec("apeswap_v2", APESWAP_V2_ROUTER, 30),
    "dfyn": V2RouterSpec("dfyn_v2", DFYN_V2_ROUTER, 30),
    "dfyn_v2": V2RouterSpec("dfyn_v2", DFYN_V2_ROUTER, 30),
    "jetswap": V2RouterSpec(
        "jetswap_v2",
        JETSWAP_V2_ROUTER,
        30,
        supported=False,
        notes="router address must be verified before live enable",
    ),
    "jetswap_v2": V2RouterSpec(
        "jetswap_v2",
        JETSWAP_V2_ROUTER,
        30,
        supported=False,
        notes="router address must be verified before live enable",
    ),
}


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


def get_v2_router(router_name: str) -> V2RouterSpec:
    key = router_name.lower().replace("-", "_").strip()
    try:
        spec = V2_ROUTER_REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"unknown V2 router {router_name!r}") from exc
    if not spec.supported:
        raise ValueError(f"V2 router {spec.name!r} is not live-supported: {spec.notes}")
    return spec


def encode_v2_swap_exact_tokens_for_tokens(
    amount_in: int,
    min_amount_out: int,
    path: list[str],
    recipient: str,
    deadline: int,
) -> bytes:
    if len(path) < 2:
        raise ValueError("V2 path must have at least two tokens")
    if int(amount_in) <= 0:
        raise ValueError("V2 amount_in must be positive")
    if int(min_amount_out) <= 0:
        raise ValueError("V2 min_amount_out must be positive")
    if Web3.to_checksum_address(path[0]) == Web3.to_checksum_address(path[-1]):
        raise ValueError("V2 path cannot start and end with the same token")
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
    return build_uniswap_v2_like_step(
        router_name="quickswap_v2",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        recipient=recipient,
        deadline=deadline,
        fee_bps=fee_bps,
    )


def build_uniswap_v2_like_step(
    router_name: str,
    token_in: str,
    token_out: str,
    amount_in: int,
    min_amount_out: int,
    recipient: str,
    deadline: int,
    fee_bps: int | None = None,
    path: list[str] | None = None,
) -> dict[str, Any]:
    spec = get_v2_router(router_name)
    route_path = path or [token_in, token_out]
    data = encode_v2_swap_exact_tokens_for_tokens(
        amount_in,
        min_amount_out,
        route_path,
        recipient,
        deadline,
    )
    return EncodedStep(
        protocol=PROTOCOL_UNISWAP_V2,
        target=spec.router,
        approve_token=token_in,
        output_token=token_out,
        call_value=0,
        min_amount_in=int(amount_in),
        min_amount_out=int(min_amount_out),
        fee_bps=int(spec.fee_bps if fee_bps is None else fee_bps),
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
        data = bytes(step.get("data", b""))
        if len(data) < 4:
            raise ValueError(f"step {idx} has missing router calldata")
        if int(step.get("minAmountIn", 0)) <= 0:
            raise ValueError(f"step {idx} requires positive minAmountIn")
        if int(step.get("minAmountOut", 0)) <= 0:
            raise ValueError(f"step {idx} requires positive minAmountOut")
        Web3.to_checksum_address(step["target"])
        Web3.to_checksum_address(step["approveToken"])
        Web3.to_checksum_address(step["outputToken"])
