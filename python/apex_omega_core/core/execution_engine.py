from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from web3 import Web3

from .execution_compiler import ExecutionCompiler, CompiledExecution
from .runtime_config import RuntimeConfig


@dataclass(frozen=True)
class ExecutionPlan:
    target: str
    compiled: CompiledExecution
    calldata: bytes


class ExecutionEngine:
    """High-level execution engine bridging strategy output to on-chain execution."""

    def __init__(self, config: RuntimeConfig, compiler: ExecutionCompiler | None = None):
        self.config = config
        self.compiler = compiler or ExecutionCompiler()
        self._w3: Web3 | None = None

    def _get_w3(self) -> Web3:
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self.config.primary_rpc))
        return self._w3

    def build_c1_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        compiled = self.compiler.compile_for_institutional(strategy_output)
        calldata = compiled.encoded_payload
        return ExecutionPlan(target="institutional", compiled=compiled, calldata=calldata)

    def build_c2_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        compiled = self.compiler.compile_for_ultimate(strategy_output)
        calldata = compiled.encoded_payload
        return ExecutionPlan(target="ultimate", compiled=compiled, calldata=calldata)

    def validate_opportunity(self, opportunity: Mapping[str, Any]) -> None:
        if opportunity.get("net_profit_usd", 0.0) < self.config.min_net_profit_usd:
            raise ValueError("Opportunity rejected: insufficient net profit")
        if opportunity.get("slippage_bps", 0.0) > self.config.max_route_slippage_bps:
            raise ValueError("Opportunity rejected: excessive slippage")
        if opportunity.get("pool_tvl_usd", 0.0) < self.config.min_pool_tvl_usd:
            raise ValueError("Opportunity rejected: insufficient pool TVL")

    def simulate_only(self, plan: ExecutionPlan) -> dict[str, Any]:
        return {
            "target": plan.target,
            "calldata_len": len(plan.calldata),
            "min_profit": plan.compiled.min_profit,
            "asset": plan.compiled.asset,
        }

    def send_transaction(self, plan: ExecutionPlan) -> str:
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
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return tx_hash.hex()
