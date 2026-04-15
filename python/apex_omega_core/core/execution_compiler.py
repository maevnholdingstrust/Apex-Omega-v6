from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from eth_abi import encode
from web3 import Web3

INSTITUTIONAL_STEP_TYPE = "(uint8,address,address,address,uint256,uint256,uint256,uint16,bytes)"
ULTIMATE_STEP_TYPE = "(uint8,address,address,uint256,uint256,uint256,uint16,bytes)"


@dataclass(frozen=True)
class CompiledExecution:
    encoded_payload: bytes
    min_profit: int
    asset: str


class EnvelopeCompiler:
    """Compiler that converts strategy route dicts into strict ABI payloads."""

    def encode_institutional_step(self, step: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            int(step["protocol"]),
            Web3.to_checksum_address(step["target"]),
            Web3.to_checksum_address(step["approveToken"]),
            Web3.to_checksum_address(step["outputToken"]),
            int(step.get("callValue", 0)),
            int(step.get("minAmountIn", 0)),
            int(step.get("minAmountOut", 0)),
            int(step.get("feeBps", 0)),
            bytes(step.get("data", b"")),
        )

    def encode_ultimate_step(self, step: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            int(step["protocol"]),
            Web3.to_checksum_address(step["target"]),
            Web3.to_checksum_address(step["approveToken"]),
            int(step.get("callValue", 0)),
            int(step.get("minAmountIn", 0)),
            int(step.get("minAmountOut", 0)),
            int(step.get("feeBps", 0)),
            bytes(step.get("data", b"")),
        )

    def build_institutional_envelope(self, route: Mapping[str, Any]) -> bytes:
        steps = [self.encode_institutional_step(step) for step in route["steps"]]
        if not steps:
            raise ValueError("institutional envelope requires at least one step")

        return encode(
            ["uint8", "address", "uint256", "uint256", f"{INSTITUTIONAL_STEP_TYPE}[]"],
            [
                int(route.get("version", 1)),
                Web3.to_checksum_address(route["profitToken"]),
                int(route.get("gasReserveAsset", 0)),
                int(route.get("dexFeeReserveAsset", 0)),
                steps,
            ],
        )

    def build_ultimate_envelope(self, route: Mapping[str, Any]) -> bytes:
        steps = [self.encode_ultimate_step(step) for step in route["steps"]]
        if not steps:
            raise ValueError("ultimate envelope requires at least one step")

        return encode(
            ["uint8", "address", "uint256", "uint256", f"{ULTIMATE_STEP_TYPE}[]"],
            [
                int(route.get("version", 1)),
                Web3.to_checksum_address(route["profitToken"]),
                int(route.get("gasReserveAsset", 0)),
                int(route.get("dexFeeReserveAsset", 0)),
                steps,
            ],
        )


class FlashloanPayloadBuilder:
    """Build callback payloads for InstitutionalExecutor flashloan callbacks."""

    @staticmethod
    def build_aave_payload(min_profit: int, route_envelope: bytes) -> bytes:
        return encode(["uint256", "bytes"], [int(min_profit), bytes(route_envelope)])

    @staticmethod
    def build_balancer_payload(asset: str, amount: int, min_profit: int, route_envelope: bytes) -> bytes:
        return encode(
            ["address", "uint256", "uint256", "bytes"],
            [Web3.to_checksum_address(asset), int(amount), int(min_profit), bytes(route_envelope)],
        )


class ExecutionCompiler:
    """Compile strategy output into deterministic contract payloads."""

    def __init__(self, envelope_compiler: EnvelopeCompiler | None = None):
        self.envelope_compiler = envelope_compiler or EnvelopeCompiler()

    def compile_for_institutional(self, strategy_output: Mapping[str, Any]) -> CompiledExecution:
        route = {
            "version": 1,
            "profitToken": strategy_output["asset"],
            "gasReserveAsset": int(strategy_output.get("gas_reserve_asset", 0)),
            "dexFeeReserveAsset": int(strategy_output.get("dex_fee_reserve_asset", 0)),
            "steps": list(strategy_output["steps"]),
        }
        encoded_payload = self.envelope_compiler.build_institutional_envelope(route)
        return CompiledExecution(
            encoded_payload=encoded_payload,
            min_profit=int(strategy_output["min_profit"]),
            asset=Web3.to_checksum_address(strategy_output["asset"]),
        )

    def compile_for_ultimate(self, strategy_output: Mapping[str, Any]) -> CompiledExecution:
        route = {
            "version": 1,
            "profitToken": strategy_output["asset"],
            "gasReserveAsset": int(strategy_output.get("gas_reserve_asset", 0)),
            "dexFeeReserveAsset": int(strategy_output.get("dex_fee_reserve_asset", 0)),
            "steps": list(strategy_output["steps"]),
        }
        encoded_payload = self.envelope_compiler.build_ultimate_envelope(route)
        return CompiledExecution(
            encoded_payload=encoded_payload,
            min_profit=int(strategy_output["min_profit"]),
            asset=Web3.to_checksum_address(strategy_output["asset"]),
        )

    @staticmethod
    def merkle_leaf(encoded_payload: bytes) -> bytes:
        return Web3.keccak(encoded_payload)


def compile_strategy_batch(
    compiler: ExecutionCompiler,
    strategy_outputs: Sequence[Mapping[str, Any]],
    target: str,
) -> List[CompiledExecution]:
    if target not in {"institutional", "ultimate"}:
        raise ValueError("target must be either 'institutional' or 'ultimate'")

    compiled: List[CompiledExecution] = []
    for output in strategy_outputs:
        if target == "institutional":
            compiled.append(compiler.compile_for_institutional(output))
        else:
            compiled.append(compiler.compile_for_ultimate(output))
    return compiled
