"""Live execution trigger router for the three Apex-Omega strategy lanes.

The router keeps each lane independent:

1. C1 VM payload lane
2. C2 continuation lane
3. Liquidation lane

Each lane uses ContractInvoker semantics for live eth_call, gas estimate, signing,
and optional broadcast.  This file does not quote prices or invent calldata.
Callers must supply already-built payloads or calldata from the correct strategy
builder.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.live_contract_invoker import LiveContractInvoker
from apex_omega_core.core.executable_payloads import FlashloanIntegratedC1Payload
from apex_omega_core.core.contract_targets import C1_TARGET, C2_TARGET


@dataclass(frozen=True)
class C2TriggerContext:
    parent_redis_id: str
    c1_tx_hash: str
    c1_block: int
    current_block: int
    max_delay_blocks: int = 5

    def validate(self) -> None:
        if not self.parent_redis_id:
            raise ValueError("parent_redis_id is required")
        if not isinstance(self.c1_tx_hash, str) or not self.c1_tx_hash.startswith("0x"):
            raise ValueError("c1_tx_hash must be a transaction hash")
        if int(self.current_block) <= int(self.c1_block):
            raise ValueError("C2 requires a block after confirmed C1")
        if int(self.current_block) > int(self.c1_block) + int(self.max_delay_blocks):
            raise ValueError("C2 trigger window expired")


@dataclass(frozen=True)
class RawStrategyCall:
    target: str
    calldata: str
    p_net_usd: float
    context: Mapping[str, Any]

    def validate(self) -> None:
        if not isinstance(self.target, str) or not self.target.startswith("0x") or len(self.target) != 42:
            raise ValueError("target must be an EVM address")
        if not isinstance(self.calldata, str) or not self.calldata.startswith("0x") or len(self.calldata) <= 2:
            raise ValueError("calldata must be non-empty hex")


class LiveStrategyTriggerRouter:
    """Three-lane live execution router."""

    def __init__(self, rpc_url: Optional[str] = None) -> None:
        self.rpc_url = rpc_url
        self.c1_invoker = LiveContractInvoker(os.getenv("APEX_C1_VM_TARGET", C1_TARGET), rpc_url=rpc_url)
        self.c2_invoker = ContractInvoker(os.getenv("APEX_C2_TARGET", C2_TARGET), rpc_url=rpc_url)
        liquidation_target = os.getenv("APEX_LIQUIDATION_TARGET") or os.getenv("LIQUIDATION_EXECUTOR_TARGET")
        self.liquidation_invoker = ContractInvoker(liquidation_target, rpc_url=rpc_url) if liquidation_target else None

    def trigger_c1(
        self,
        payload: FlashloanIntegratedC1Payload,
        record: Mapping[str, Any] | None,
        *,
        current_block: Optional[int] = None,
    ) -> dict:
        return self.c1_invoker.invoke_live_c1_payload(payload, record, current_block=current_block)

    def trigger_c2(self, call: RawStrategyCall, c2_context: C2TriggerContext) -> dict:
        call.validate()
        c2_context.validate()
        if call.target.lower() != self.c2_invoker.target_address.lower():
            raise ValueError("C2 target mismatch")
        ctx = dict(call.context)
        ctx.setdefault("parent_redis_id", c2_context.parent_redis_id)
        ctx.setdefault("c1_tx_hash", c2_context.c1_tx_hash)
        ctx.setdefault("c1_block", c2_context.c1_block)
        ctx.setdefault("current_block", c2_context.current_block)
        ctx.setdefault("max_delay_blocks", c2_context.max_delay_blocks)
        ctx.setdefault("token_pair", "C2_CONTINUATION")
        return self.c2_invoker.invoke(call.calldata, p_net_usd=float(call.p_net_usd), execution_context=ctx)

    def trigger_liquidation(self, call: RawStrategyCall) -> dict:
        call.validate()
        if self.liquidation_invoker is None:
            raise ValueError("liquidation target is not configured")
        if call.target.lower() != self.liquidation_invoker.target_address.lower():
            raise ValueError("liquidation target mismatch")
        ctx = dict(call.context)
        ctx.setdefault("token_pair", "LIQUIDATION")
        return self.liquidation_invoker.invoke(call.calldata, p_net_usd=float(call.p_net_usd), execution_context=ctx)
