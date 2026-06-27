"""Live VM invoker extension for Apex-Omega strategies.

This module keeps the existing ContractInvoker behavior available while adding a
strict payload-based C1 path for the execution VM.  C2 and liquidation callers
can continue using their dedicated strategy contracts until their own VM context
adapters are connected.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from .c1_payload_gate import C1GateConfig, C1PayloadGate
from .contract_invoker import ContractInvoker
from .executable_payloads import FlashloanIntegratedC1Payload
from .execution_vm_calldata import BuiltCalldata, build_c1_vm_calldata


class LiveContractInvoker(ContractInvoker):
    """ContractInvoker with a strict C1 VM payload entrypoint."""

    def build_live_c1_calldata(
        self,
        payload: FlashloanIntegratedC1Payload,
        record: Mapping[str, Any] | None,
        *,
        current_block: Optional[int] = None,
    ) -> BuiltCalldata:
        built = build_c1_vm_calldata(payload)
        gate = C1PayloadGate(
            C1GateConfig(
                min_net_profit_usd=float(os.getenv("MIN_NET_PROFIT_USD", "5")),
                current_block=current_block,
                expected_target_contract=self.target_address,
            )
        )
        checked = gate.validate(payload, record, built_calldata_hash=built.calldataHash)
        checked.raise_if_failed()
        return built

    def invoke_live_c1_payload(
        self,
        payload: FlashloanIntegratedC1Payload,
        record: Mapping[str, Any] | None,
        *,
        current_block: Optional[int] = None,
        execution_context: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        built = self.build_live_c1_calldata(payload, record, current_block=current_block)
        ctx = dict(execution_context or {})
        ctx.setdefault("opportunity_id", payload.redisId)
        ctx.setdefault("token_pair", "C1_VM")
        ctx.setdefault("loan_amount_usd", payload.metadata.get("loanAmountUsd"))
        ctx.setdefault("expected_profit_usd", payload.netProfitUsd)
        ctx.setdefault("min_profit", payload.context.minNetProfit)
        ctx.setdefault("calldata_hash", built.calldataHash)
        return self.invoke(built.calldata, p_net_usd=payload.netProfitUsd, execution_context=ctx)
