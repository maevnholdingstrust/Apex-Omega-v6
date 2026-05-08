from __future__ import annotations

from typing import Any, Mapping, Sequence

from eth_abi import encode
from web3 import Web3

PROTOCOL_CUSTOM = 0
PROTOCOL_UNISWAP_V2 = 1
PROTOCOL_UNISWAP_V3 = 2
PROTOCOL_ALGEBRA = 3
PROTOCOL_CURVE = 4
PROTOCOL_BALANCER = 5
STEP_PROTOCOL_MASK = 0x7F
MAX_BPS = 10_000
DEFAULT_DEADLINE = 2**256 - 1


def min_amount_out_from_quote(amount_out_quote: int, slippage_bps: int) -> int:
    if amount_out_quote < 0:
        raise ValueError("amount_out_quote must be non-negative")
    if slippage_bps < 0 or slippage_bps > MAX_BPS:
        raise ValueError("slippage_bps must be in [0, 10000]")
    return (int(amount_out_quote) * (MAX_BPS - int(slippage_bps))) // MAX_BPS


class ProtocolSwapEncoder:
    @staticmethod
    def _selector(signature: str) -> bytes:
        return Web3.keccak(text=signature)[:4]

    @staticmethod
    def _address(value: str) -> str:
        return Web3.to_checksum_address(value)

    @staticmethod
    def _bytes32(value: str | bytes) -> bytes:
        if isinstance(value, bytes):
            if len(value) != 32:
                raise ValueError("bytes poolId must be exactly 32 bytes")
            return value
        if isinstance(value, str) and value.startswith("0x"):
            raw = bytes.fromhex(value[2:])
            if len(raw) != 32:
                raise ValueError("hex poolId must be exactly 32 bytes")
            return raw
        raise ValueError("poolId must be 32-byte hex string or bytes")

    @staticmethod
    def resolve_min_amount_out(step: Mapping[str, Any], required: bool = True) -> int:
        if "minAmountOut" in step:
            return int(step["minAmountOut"])
        if "amountOutMin" in step:
            return int(step["amountOutMin"])
        if "amountOutQuote" in step:
            return min_amount_out_from_quote(
                int(step["amountOutQuote"]),
                int(step.get("slippageBps", 0)),
            )
        if required:
            raise KeyError("Missing minAmountOut/amountOutMin/amountOutQuote in protocol step")
        return 0

    @staticmethod
    def _required_address_from(step: Mapping[str, Any], keys: Sequence[str], label: str) -> str:
        for key in keys:
            value = step.get(key)
            if value:
                return ProtocolSwapEncoder._address(value)
        raise KeyError(f"Missing required address field for {label}: expected one of {list(keys)}")

    @staticmethod
    def _resolve_amount_in(step: Mapping[str, Any]) -> int:
        if "minAmountIn" in step:
            return int(step["minAmountIn"])
        if "amountIn" in step:
            return int(step["amountIn"])
        raise KeyError("Missing minAmountIn/amountIn in protocol step")

    @classmethod
    def encode_uniswap_v2(cls, step: Mapping[str, Any]) -> bytes:
        token_in = step.get("tokenIn")
        token_out = step.get("tokenOut")
        raw_path: Sequence[str] | None = step.get("path")
        if raw_path is None:
            if token_in is None or token_out is None:
                raise KeyError("UniswapV2 step requires path or tokenIn/tokenOut")
            raw_path = [token_in, token_out]
        path = [cls._address(addr) for addr in raw_path]
        recipient = cls._address(step["recipient"])
        deadline = int(step.get("deadline", DEFAULT_DEADLINE))
        amount_in = cls._resolve_amount_in(step)
        amount_out_min = cls.resolve_min_amount_out(step)
        args = [amount_in, amount_out_min, path, recipient, deadline]
        return cls._selector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
            ["uint256", "uint256", "address[]", "address", "uint256"],
            args,
        )

    @classmethod
    def encode_uniswap_v3(cls, step: Mapping[str, Any]) -> bytes:
        amount_in = cls._resolve_amount_in(step)
        amount_out_min = cls.resolve_min_amount_out(step)
        params = (
            cls._address(step["tokenIn"]),
            cls._address(step["tokenOut"]),
            int(step["poolFee"]),
            cls._address(step["recipient"]),
            int(step.get("deadline", DEFAULT_DEADLINE)),
            amount_in,
            amount_out_min,
            int(step.get("sqrtPriceLimitX96", 0)),
        )
        return cls._selector(
            "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
        ) + encode(
            ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
            [params],
        )

    @classmethod
    def encode_algebra(cls, step: Mapping[str, Any]) -> bytes:
        amount_in = cls._resolve_amount_in(step)
        amount_out_min = cls.resolve_min_amount_out(step)
        params = (
            cls._address(step["tokenIn"]),
            cls._address(step["tokenOut"]),
            cls._address(step["recipient"]),
            int(step.get("deadline", DEFAULT_DEADLINE)),
            amount_in,
            amount_out_min,
            int(step.get("limitSqrtPrice", 0)),
        )
        return cls._selector(
            "exactInputSingle((address,address,address,uint256,uint256,uint256,uint160))"
        ) + encode(
            ["(address,address,address,uint256,uint256,uint256,uint160)"],
            [params],
        )

    @classmethod
    def encode_curve(cls, step: Mapping[str, Any]) -> bytes:
        amount_in = cls._resolve_amount_in(step)
        amount_out_min = cls.resolve_min_amount_out(step)
        args = [
            cls._address(step["pool"]),
            cls._address(step["tokenIn"]),
            cls._address(step["tokenOut"]),
            amount_in,
            amount_out_min,
            cls._address(step["recipient"]),
        ]
        return cls._selector(
            "exchange(address,address,address,uint256,uint256,address)"
        ) + encode(
            ["address", "address", "address", "uint256", "uint256", "address"],
            args,
        )

    @classmethod
    def encode_balancer(cls, step: Mapping[str, Any]) -> bytes:
        amount_in = cls._resolve_amount_in(step)
        amount_out_min = cls.resolve_min_amount_out(step)
        pool_id = cls._bytes32(step["poolId"])
        asset_in = cls._required_address_from(step, ("assetIn", "tokenIn", "approveToken"), "assetIn")
        asset_out = cls._required_address_from(step, ("assetOut", "tokenOut", "outputToken"), "assetOut")
        sender = cls._address(step.get("sender", step["recipient"]))
        recipient = cls._address(step["recipient"])
        single_swap = (
            pool_id,
            int(step.get("swapKind", 0)),
            asset_in,
            asset_out,
            amount_in,
            bytes(step.get("userData", b"")),
        )
        funds = (
            sender,
            bool(step.get("fromInternalBalance", False)),
            recipient,
            bool(step.get("toInternalBalance", False)),
        )
        args = [single_swap, funds, amount_out_min, int(step.get("deadline", DEFAULT_DEADLINE))]
        return cls._selector(
            "swap((bytes32,uint8,address,address,uint256,bytes),(address,bool,address,bool),uint256,uint256)"
        ) + encode(
            [
                "(bytes32,uint8,address,address,uint256,bytes)",
                "(address,bool,address,bool)",
                "uint256",
                "uint256",
            ],
            args,
        )

    @classmethod
    def encode_protocol_step(cls, step: Mapping[str, Any]) -> bytes:
        protocol = int(step["protocol"]) & STEP_PROTOCOL_MASK
        if protocol == PROTOCOL_UNISWAP_V2:
            return cls.encode_uniswap_v2(step)
        if protocol == PROTOCOL_UNISWAP_V3:
            return cls.encode_uniswap_v3(step)
        if protocol == PROTOCOL_ALGEBRA:
            return cls.encode_algebra(step)
        if protocol == PROTOCOL_CURVE:
            return cls.encode_curve(step)
        if protocol == PROTOCOL_BALANCER:
            return cls.encode_balancer(step)
        if protocol == PROTOCOL_CUSTOM:
            raise ValueError("Custom protocol requires explicit 'data'")
        raise ValueError(f"Unsupported protocol id: {protocol}")

    @classmethod
    def resolve_step_data(cls, step: Mapping[str, Any]) -> bytes:
        raw_data = step.get("data")
        if raw_data:
            return bytes(raw_data)
        return cls.encode_protocol_step(step)
