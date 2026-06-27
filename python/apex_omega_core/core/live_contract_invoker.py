"""Live VM invoker extension for Apex-Omega strategies."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .contract_invoker import ContractInvoker
from .executable_payloads import FlashloanIntegratedC1Payload
from .execution_vm_calldata import BuiltCalldata, build_c1_vm_calldata


class LiveContractInvoker(ContractInvoker):
    def build_live_c1_calldata(
        self,
        payload: FlashloanIntegratedC1Payload,
        record: Mapping[str, Any] | None,
        *,
        current_block: Optional[int] = None,
    ) -> BuiltCalldata:
        calldata = self.build_c1_calldata({"vm_payload": payload, "lock_record": record, "current_block": current_block})
        built = build_c1_vm_calldata(payload)
        if calldata.lower() != built.calldata.lower():
            raise ValueError("VM calldata mismatch")
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
        ctx.setdefault("expected_profit_usd", payload.netProfitUsd)
        ctx.setdefault("min_profit", payload.context.minNetProfit)
        ctx.setdefault("calldata_hash", built.calldataHash)
        return self.invoke(built.calldata, p_net_usd=payload.netProfitUsd, execution_context=ctx)
