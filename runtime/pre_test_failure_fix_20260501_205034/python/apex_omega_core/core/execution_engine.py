from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from eth_abi import encode
from web3 import Web3

from .execution_compiler import ExecutionCompiler, CompiledExecution
from .runtime_config import RuntimeConfig
from .relay_submitter import RelayBundleSubmitter


@dataclass(frozen=True)
class ExecutionPlan:
    target: str
    compiled: CompiledExecution
    calldata: bytes
    flash_loan_amount: int = 0
    merkle_leaf: bytes | None = None
    merkle_proof: tuple[bytes, ...] = ()


class ExecutionEngine:
    """Execution engine aligned with MEV bundle submission (C1 + C2)."""

    def __init__(self, config: RuntimeConfig, compiler: ExecutionCompiler | None = None):
        self.config = config
        self.compiler = compiler or ExecutionCompiler()
        self.relay = RelayBundleSubmitter(config)
        self._w3: Web3 | None = None

    def _get_w3(self) -> Web3:
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self.config.primary_rpc))
        return self._w3

    @staticmethod
    def _selector(signature: str) -> bytes:
        return Web3.keccak(text=signature)[:4]

    @staticmethod
    def _flash_loan_amount(strategy_output: Mapping[str, Any]) -> int:
        explicit = strategy_output.get("flash_loan_amount") or strategy_output.get("flash_loan_amount_raw")
        if explicit is not None:
            amount = int(explicit)
        else:
            steps = list(strategy_output.get("steps", []))
            if not steps:
                raise ValueError("strategy_output requires steps to derive flash-loan amount")
            amount = int(steps[0].get("minAmountIn", 0))
        if amount <= 0:
            raise ValueError("flash-loan amount must be positive base units")
        return amount

    @staticmethod
    def _merkle_proof(strategy_output: Mapping[str, Any]) -> tuple[bytes, ...]:
        proof_items = strategy_output.get("merkle_proof", ())
        proof: list[bytes] = []
        for item in proof_items:
            if isinstance(item, str):
                value = Web3.to_bytes(hexstr=item)
            else:
                value = bytes(item)
            if len(value) != 32:
                raise ValueError("C2 merkle_proof entries must be bytes32")
            proof.append(value)
        return tuple(proof)

    def build_c1_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        compiled = self.compiler.compile_for_institutional(strategy_output)
        amount = self._flash_loan_amount(strategy_output)
        provider = str(
            strategy_output.get("flash_loan_provider")
            or os.getenv("FLASH_LOAN_PROVIDER", "aave_v3")
        ).lower()
        if provider in {"balancer", "balancer_v3"}:
            calldata = self._selector("initBalancerFlash(address,uint256,uint256,bytes)") + encode(
                ["address", "uint256", "uint256", "bytes"],
                [compiled.asset, amount, compiled.min_profit, compiled.encoded_payload],
            )
        else:
            calldata = self._selector("initAaveFlash(address,uint256,uint256,bytes)") + encode(
                ["address", "uint256", "uint256", "bytes"],
                [compiled.asset, amount, compiled.min_profit, compiled.encoded_payload],
            )
        return ExecutionPlan("institutional", compiled, calldata, amount)

    def build_c2_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        compiled = self.compiler.compile_for_ultimate(strategy_output)
        amount = self._flash_loan_amount(strategy_output)
        proof = self._merkle_proof(strategy_output)
        leaf = self.compiler.merkle_leaf(compiled.encoded_payload)
        calldata = self._selector("executeArbitrage(address,uint256,uint256,bytes32[],bytes)") + encode(
            ["address", "uint256", "uint256", "bytes32[]", "bytes"],
            [compiled.asset, amount, compiled.min_profit, list(proof), compiled.encoded_payload],
        )
        return ExecutionPlan("ultimate", compiled, calldata, amount, leaf, proof)

    def validate_opportunity(self, opportunity: Mapping[str, Any]) -> None:
        if opportunity.get("net_profit_usd", 0.0) < self.config.min_net_profit_usd:
            raise ValueError("Opportunity rejected: insufficient net profit")
        if opportunity.get("slippage_bps", 0.0) > self.config.max_route_slippage_bps:
            raise ValueError("Opportunity rejected: excessive slippage")
        if opportunity.get("pool_tvl_usd", 0.0) < self.config.min_pool_tvl_usd:
            raise ValueError("Opportunity rejected: insufficient pool TVL")

    def sign_transaction(self, plan: ExecutionPlan) -> str:
        self.config.assert_safe_to_send()
        w3 = self._get_w3()
        account = w3.eth.account.from_key(self.config.executor_private_key)

        tx = {
            "to": self.config.c1_executor_address if plan.target == "institutional" else self.config.c2_executor_address,
            "data": plan.calldata,
            "chainId": self.config.chain_id,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 1_500_000,
            "maxFeePerGas": w3.to_wei(50, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
        }

        signed = account.sign_transaction(tx)
        return signed.rawTransaction.hex()

    def execute_bundle(self, raw_tx: str) -> list[Any]:
        w3 = self._get_w3()
        target_block = w3.eth.block_number + self.config.bundle_target_block_offset
        return self.relay.submit_bundle([raw_tx], target_block)

    def simulate_only(self, plan: ExecutionPlan) -> dict[str, Any]:
        return {
            "target": plan.target,
            "calldata_len": len(plan.calldata),
            "min_profit": plan.compiled.min_profit,
            "asset": plan.compiled.asset,
            "flash_loan_amount": plan.flash_loan_amount,
            "merkle_leaf": Web3.to_hex(plan.merkle_leaf) if plan.merkle_leaf else None,
            "merkle_proof_len": len(plan.merkle_proof),
        }
