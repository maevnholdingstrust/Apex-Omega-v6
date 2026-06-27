"""Build canonical C1 payloads from V2/V3 executable route legs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from .dex_execution_adapters import LiveLegQuote, build_vm_steps_from_live_quotes, validate_round_trip_steps
from .executable_payloads import (
    PAYLOAD_KIND_C1,
    STATUS_EXECUTABLE,
    STATUS_LOCKED,
    FlashloanIntegratedC1Payload,
    VmExecutionContext,
)


@dataclass(frozen=True)
class ExecutableOpportunityInput:
    redis_id: str
    target_contract: str
    route_id: str
    chain_id: int
    flashloan_source: int
    flashloan_asset: str
    flashloan_amount: int
    profit_asset: str
    min_net_profit: int
    nonce: int
    deadline_block: int
    gross_profit_usd: float
    flash_fee_usd: float
    gas_cost_usd: float
    risk_buffer_usd: float
    net_profit_usd: float
    leg1_buy_price: float
    leg2_sell_price: float
    legs: List[LiveLegQuote]
    merkle_root: str = "0x" + "00" * 32
    proof: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate_profit_identity(self) -> None:
        expected = float(self.gross_profit_usd) - float(self.flash_fee_usd) - float(self.gas_cost_usd) - float(self.risk_buffer_usd)
        if abs(float(self.net_profit_usd) - expected) > 1e-9:
            raise ValueError("net_profit_usd must equal gross - flash_fee - gas - risk_buffer")
        if float(self.leg1_buy_price) >= float(self.leg2_sell_price):
            raise ValueError("price invariant failed: leg1_buy_price must be lower than leg2_sell_price")
        if int(self.flashloan_amount) <= 0:
            raise ValueError("flashloan_amount must be positive")
        if int(self.min_net_profit) <= 0:
            raise ValueError("min_net_profit must be positive")


def build_flashloan_c1_payload(inp: ExecutableOpportunityInput) -> FlashloanIntegratedC1Payload:
    inp.validate_profit_identity()
    steps = build_vm_steps_from_live_quotes(inp.legs)
    validate_round_trip_steps(steps, inp.flashloan_asset)

    payload = FlashloanIntegratedC1Payload(
        payloadKind=PAYLOAD_KIND_C1,
        redisId=inp.redis_id,
        targetContract=inp.target_contract,
        chainId=int(inp.chain_id),
        routeId=inp.route_id,
        candidateStatus=STATUS_EXECUTABLE,
        lockStatus=STATUS_LOCKED,
        flashloanSource=int(inp.flashloan_source),
        flashloanAsset=inp.flashloan_asset,
        flashloanAmount=int(inp.flashloan_amount),
        context=VmExecutionContext(
            profitAsset=inp.profit_asset,
            minNetProfit=int(inp.min_net_profit),
            nonce=int(inp.nonce),
            merkleRoot=inp.merkle_root,
            proof=list(inp.proof),
            steps=steps,
        ),
        grossProfitUsd=float(inp.gross_profit_usd),
        flashFeeUsd=float(inp.flash_fee_usd),
        gasCostUsd=float(inp.gas_cost_usd),
        riskBufferUsd=float(inp.risk_buffer_usd),
        netProfitUsd=float(inp.net_profit_usd),
        leg1BuyPrice=float(inp.leg1_buy_price),
        leg2SellPrice=float(inp.leg2_sell_price),
        deadlineBlock=int(inp.deadline_block),
        metadata={**dict(inp.metadata), "builtAtUnix": time.time(), "routeFamilies": [str(leg.family.value) for leg in inp.legs]},
    )
    payload.assert_route_invariants()
    return payload


def build_payload_from_quote_legs(**kwargs: Any) -> FlashloanIntegratedC1Payload:
    return build_flashloan_c1_payload(ExecutableOpportunityInput(**kwargs))
