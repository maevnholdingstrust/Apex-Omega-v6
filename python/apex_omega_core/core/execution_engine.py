from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from eth_abi import decode, encode
from eth_account import Account
from web3 import Web3

from .execution_compiler import APEX_VM_CONTEXT_TYPE, ExecutionCompiler, CompiledExecution
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

    MAX_FLASH_TVL_FRACTION = 0.15

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

    def _use_apex_vm(self) -> bool:
        mode = os.getenv("EXECUTION_CONTRACT_MODE", "").strip().lower()
        if mode in {"apex_vm", "vm", "unified_vm"}:
            return True
        if mode in {"legacy", "split"}:
            return False
        try:
            c1 = Web3.to_checksum_address(self.config.c1_executor_address)
            c2 = Web3.to_checksum_address(self.config.c2_executor_address)
        except ValueError:
            return False
        return c1 == c2

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

    @staticmethod
    def _flashloan_source(strategy_output: Mapping[str, Any]) -> int:
        provider = str(
            strategy_output.get("flash_loan_provider")
            or os.getenv("FLASH_LOAN_PROVIDER", "aave_v3")
        ).lower()
        if provider in {"aave", "aave_v3"}:
            return 0
        if provider in {"balancer", "balancer_v2"}:
            return 1
        if provider in {"balancer_v3"}:
            return 2
        raise ValueError(f"unsupported flash loan provider for Apex VM: {provider}")

    @staticmethod
    def _checksum_address(value: Any, label: str) -> str:
        if not value:
            raise ValueError(f"{label} is required")
        try:
            return Web3.to_checksum_address(str(value))
        except ValueError as exc:
            raise ValueError(f"{label} is not a valid EVM address") from exc

    def _assert_c1_flash_loan_receiver(self, strategy_output: Mapping[str, Any]) -> str:
        expected = self._checksum_address(self.config.c1_executor_address, "C1 executor address")
        declared_items = [
            (key, strategy_output.get(key))
            for key in ("flash_loan_receiver", "executor_address")
            if strategy_output.get(key)
        ]
        if not declared_items:
            raise ValueError("strategy_output requires flash_loan_receiver or executor_address for C1 flashloan")
        for key, value in declared_items:
            receiver = self._checksum_address(value, key)
            if receiver != expected:
                raise ValueError("C1 flashloan receiver must equal configured C1 executor address")
        return expected

    def build_c1_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        if self._use_apex_vm():
            return self.build_vm_c1_plan(strategy_output)
        self._assert_c1_flash_loan_receiver(strategy_output)
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
        if self._use_apex_vm():
            return self.build_vm_c2_plan(strategy_output)
        compiled = self.compiler.compile_for_ultimate(strategy_output)
        amount = self._flash_loan_amount(strategy_output)
        proof = self._merkle_proof(strategy_output)
        leaf = self.compiler.merkle_leaf(compiled.encoded_payload)
        calldata = self._selector("executeArbitrage(address,uint256,uint256,bytes32[],bytes)") + encode(
            ["address", "uint256", "uint256", "bytes32[]", "bytes"],
            [compiled.asset, amount, compiled.min_profit, list(proof), compiled.encoded_payload],
        )
        return ExecutionPlan("ultimate", compiled, calldata, amount, leaf, proof)

    def build_vm_c1_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        self._assert_c1_flash_loan_receiver(strategy_output)
        compiled = self.compiler.compile_for_apex_vm(strategy_output)
        amount = self._flash_loan_amount(strategy_output)
        source = self._flashloan_source(strategy_output)
        context = decode([APEX_VM_CONTEXT_TYPE], compiled.encoded_payload)[0]
        calldata = self._selector(
            "executeC1(uint8,address,uint256,(address,uint256,uint256,bytes32,bytes32[],(address,address,address,uint256,uint256,uint256,bytes)[]))"
        ) + encode(
            ["uint8", "address", "uint256", APEX_VM_CONTEXT_TYPE],
            [source, compiled.asset, amount, context],
        )
        return ExecutionPlan("apex_vm_c1", compiled, calldata, amount)

    def build_vm_c2_plan(self, strategy_output: Mapping[str, Any]) -> ExecutionPlan:
        c1_internal_id = strategy_output.get("c1_internal_id")
        if not c1_internal_id:
            raise ValueError("Apex VM C2 requires c1_internal_id from confirmed C1")
        c1_internal_id_bytes = Web3.to_bytes(hexstr=c1_internal_id) if isinstance(c1_internal_id, str) else bytes(c1_internal_id)
        if len(c1_internal_id_bytes) != 32:
            raise ValueError("Apex VM C2 c1_internal_id must be bytes32")

        compiled = self.compiler.compile_for_apex_vm(strategy_output)
        amount = self._flash_loan_amount(strategy_output)
        source = self._flashloan_source(strategy_output)
        context = decode([APEX_VM_CONTEXT_TYPE], compiled.encoded_payload)[0]
        calldata = self._selector(
            "executeC2(bytes32,uint8,address,uint256,(address,uint256,uint256,bytes32,bytes32[],(address,address,address,uint256,uint256,uint256,bytes)[]))"
        ) + encode(
            ["bytes32", "uint8", "address", "uint256", APEX_VM_CONTEXT_TYPE],
            [c1_internal_id_bytes, source, compiled.asset, amount, context],
        )
        return ExecutionPlan("apex_vm_c2", compiled, calldata, amount)

    @staticmethod
    def _num(opportunity: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
        for key in keys:
            value = opportunity.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return float(default)

    def _net_profit_usd(self, opportunity: Mapping[str, Any]) -> float:
        explicit = opportunity.get("net_profit_usd")
        if explicit is not None and explicit != "":
            return self._num(opportunity, "net_profit_usd")

        loan_amount = self._num(
            opportunity,
            "loan_amount_usd",
            "flash_loan_amount_usd",
            "loan_amount",
            "flash_loan_amount",
        )
        gross_profit = self._num(opportunity, "gross_profit_usd")
        final_output = self._num(opportunity, "final_output_usd", "final_output", default=0.0)
        if gross_profit == 0.0 and final_output > 0.0 and loan_amount > 0.0:
            gross_profit = final_output - loan_amount

        flash_fee = self._num(
            opportunity,
            "flashloan_fee_usd",
            "flash_loan_fee_usd",
            default=loan_amount * self.config.flash_loan_fee_bps / 10_000.0,
        )
        gas_cost = self._num(opportunity, "gas_cost_usd", "gas_usd")
        dex_fees = self._num(opportunity, "dex_fees_usd", "dex_fee_usd")
        protocol_fees = self._num(opportunity, "protocol_fees_usd", "protocol_fee_usd")
        risk_buffer = self._num(opportunity, "risk_buffer_usd", default=self.config.risk_buffer_usd)
        return gross_profit - flash_fee - gas_cost - dex_fees - protocol_fees - risk_buffer

    def validate_opportunity(self, opportunity: Mapping[str, Any]) -> None:
        net_profit = self._net_profit_usd(opportunity)
        if net_profit < self.config.min_net_profit_usd:
            raise ValueError("Opportunity rejected: insufficient net profit")

        pool_tvl = self._num(opportunity, "weakest_pool_tvl_usd", "pool_tvl_usd", "tvl_usd")
        if pool_tvl < self.config.min_pool_tvl_usd:
            raise ValueError("Opportunity rejected: insufficient pool TVL")

        loan_amount = self._num(
            opportunity,
            "loan_amount_usd",
            "flash_loan_amount_usd",
            "loan_amount",
            "flash_loan_amount",
        )
        if loan_amount > 0.0 and pool_tvl > 0.0 and loan_amount > pool_tvl * self.MAX_FLASH_TVL_FRACTION:
            raise ValueError("Opportunity rejected: excessive liquidity impact")

        final_output = self._num(opportunity, "final_output_usd", "final_output")
        minimum_final_output = self._num(opportunity, "minimum_final_output", "min_return_amount")
        if final_output > 0.0 and minimum_final_output > 0.0 and final_output < minimum_final_output:
            raise ValueError("Opportunity rejected: final output below repayment and profit floor")

    def sign_transaction(self, plan: ExecutionPlan) -> str:
        self.config.assert_safe_to_send()
        w3 = self._get_w3()
        account = w3.eth.account.from_key(self.config.executor_private_key)

        tx = {
            "to": self.config.c1_executor_address if plan.target in {"institutional", "apex_vm_c1"} else self.config.c2_executor_address,
            "data": plan.calldata,
            "chainId": self.config.chain_id,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 1_500_000,
            "maxFeePerGas": w3.to_wei(50, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
        }

        signed = account.sign_transaction(tx)
        raw_transaction = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        return Web3.to_hex(raw_transaction)

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

    def eth_call_plan(
        self,
        plan: ExecutionPlan,
        *,
        rpc_url: str | None = None,
        from_address: str | None = None,
    ) -> dict[str, Any]:
        """Run a no-broadcast call proof for a compiled execution plan."""
        url = rpc_url or os.getenv("FORK_RPC_URL") or self.config.primary_rpc
        if not url:
            return {"ok": False, "error": "simulation RPC is not configured"}
        if from_address:
            sender = Web3.to_checksum_address(from_address)
        elif self.config.executor_private_key:
            sender = Account.from_key(self.config.executor_private_key).address
        else:
            sender = self.config.expected_executor_address
        if not sender:
            return {"ok": False, "error": "simulation sender is not configured"}

        target = (
            self.config.c1_executor_address
            if plan.target in {"institutional", "apex_vm_c1"}
            else self.config.c2_executor_address
        )
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
        try:
            checksum_target = Web3.to_checksum_address(target)
            code = w3.eth.get_code(checksum_target)
            if not code:
                return {
                    "ok": False,
                    "rpc": url,
                    "target": checksum_target,
                    "error": "target has no bytecode on selected RPC",
                }
            tx = {
                "from": Web3.to_checksum_address(sender),
                "to": checksum_target,
                "data": Web3.to_hex(plan.calldata),
                "value": 0,
            }
            gas_estimate = None
            gas_error = None
            try:
                gas_estimate = int(w3.eth.estimate_gas(tx))
            except Exception as exc:  # noqa: BLE001
                gas_error = str(exc)
            output = w3.eth.call(tx, block_identifier="latest")
            return {
                "ok": True,
                "rpc": url,
                "chain_id": int(w3.eth.chain_id),
                "block_number": int(w3.eth.block_number),
                "target": checksum_target,
                "from": Web3.to_checksum_address(sender),
                "gas_estimate": gas_estimate,
                "gas_error": gas_error,
                "output": Web3.to_hex(output),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "rpc": url,
                "target": target,
                "from": sender,
                "error": str(exc),
            }
