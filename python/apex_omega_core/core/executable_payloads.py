"""Canonical executable payload models for Apex-Omega live C1 execution.

This module is intentionally strict.  It contains no mock pricing, no synthetic
reserves, and no dashboard-only fields.  Objects defined here are the boundary
between opportunity discovery and the execution VM.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

ZERO_HASH = "0x" + "00" * 32
PAYLOAD_KIND_C1 = "FLASHLOAN_INTEGRATED_C1_PAYLOADS"
STATUS_EXECUTABLE = "EXECUTABLE_PROFIT_CANDIDATE"
STATUS_LOCKED = "LOCKED_FOR_EXECUTION"


def _norm_addr(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        raise ValueError(f"invalid EVM address: {value!r}")
    return value.lower()


def _require_positive_int(name: str, value: int) -> int:
    ivalue = int(value)
    if ivalue <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return ivalue


@dataclass(frozen=True)
class VmStep:
    """One executable VM swap step.

    `venue` is the adapter/router/vault/pool target invoked by the VM.  `payload`
    must be already-encoded calldata for that target or adapter.  Empty payloads
    are rejected because they cannot represent a live executable swap.
    """

    venue: str
    tokenIn: str
    tokenOut: str
    amountIn: int
    minAmountOut: int
    callValue: int = 0
    payload: str = ""

    def validate(self) -> None:
        _norm_addr(self.venue)
        _norm_addr(self.tokenIn)
        _norm_addr(self.tokenOut)
        _require_positive_int("step.amountIn", self.amountIn)
        _require_positive_int("step.minAmountOut", self.minAmountOut)
        if int(self.callValue) < 0:
            raise ValueError("step.callValue cannot be negative")
        if not isinstance(self.payload, str) or not self.payload.startswith("0x") or len(self.payload) <= 2:
            raise ValueError("step.payload must be non-empty hex calldata")


@dataclass(frozen=True)
class VmExecutionContext:
    profitAsset: str
    minNetProfit: int
    nonce: int
    merkleRoot: str = ZERO_HASH
    proof: List[str] = field(default_factory=list)
    steps: List[VmStep] = field(default_factory=list)

    def validate(self) -> None:
        _norm_addr(self.profitAsset)
        _require_positive_int("context.minNetProfit", self.minNetProfit)
        if int(self.nonce) < 0:
            raise ValueError("context.nonce cannot be negative")
        if not isinstance(self.merkleRoot, str) or not self.merkleRoot.startswith("0x") or len(self.merkleRoot) != 66:
            raise ValueError("context.merkleRoot must be bytes32 hex")
        if len(self.steps) < 2:
            raise ValueError("context.steps must contain at least 2 swap legs")
        for step in self.steps:
            step.validate()

    @property
    def start_token(self) -> str:
        return self.steps[0].tokenIn.lower()

    @property
    def final_token(self) -> str:
        return self.steps[-1].tokenOut.lower()


@dataclass(frozen=True)
class FlashloanIntegratedC1Payload:
    payloadKind: Literal["FLASHLOAN_INTEGRATED_C1_PAYLOADS"]
    redisId: str
    targetContract: str
    chainId: int
    routeId: str
    candidateStatus: str
    lockStatus: str
    flashloanSource: int
    flashloanAsset: str
    flashloanAmount: int
    context: VmExecutionContext
    grossProfitUsd: float
    flashFeeUsd: float
    gasCostUsd: float
    riskBufferUsd: float
    netProfitUsd: float
    leg1BuyPrice: float
    leg2SellPrice: float
    deadlineBlock: int
    calldataHash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate_shape(self) -> None:
        if self.payloadKind != PAYLOAD_KIND_C1:
            raise ValueError(f"invalid payloadKind: {self.payloadKind}")
        if int(self.chainId) != 137:
            raise ValueError(f"chainId must be Polygon 137, got {self.chainId}")
        if not self.redisId:
            raise ValueError("redisId is required")
        if not self.routeId:
            raise ValueError("routeId is required")
        _norm_addr(self.targetContract)
        _norm_addr(self.flashloanAsset)
        _require_positive_int("flashloanAmount", self.flashloanAmount)
        if int(self.flashloanSource) not in (1, 2):
            raise ValueError("flashloanSource must be 1=Aave or 2=Balancer")
        if self.candidateStatus != STATUS_EXECUTABLE:
            raise ValueError(f"candidateStatus must be {STATUS_EXECUTABLE}")
        if self.lockStatus != STATUS_LOCKED:
            raise ValueError(f"lockStatus must be {STATUS_LOCKED}")
        if float(self.netProfitUsd) <= 0:
            raise ValueError("netProfitUsd must be positive")
        if float(self.leg1BuyPrice) >= float(self.leg2SellPrice):
            raise ValueError("price invariant failed: leg1BuyPrice must be < leg2SellPrice")
        self.context.validate()

    def assert_route_invariants(self) -> None:
        self.validate_shape()
        if self.context.start_token != self.flashloanAsset.lower():
            raise ValueError("route start token must equal flashloanAsset")
        if self.context.final_token != self.flashloanAsset.lower():
            raise ValueError("route final token must equal flashloanAsset")
        if self.context.profitAsset.lower() != self.flashloanAsset.lower():
            raise ValueError("profitAsset must equal flashloanAsset for round-trip C1")
        for idx in range(len(self.context.steps) - 1):
            left = self.context.steps[idx]
            right = self.context.steps[idx + 1]
            if left.tokenOut.lower() != right.tokenIn.lower():
                raise ValueError(
                    f"broken step chain at leg {idx}: {left.tokenOut} != {right.tokenIn}"
                )

    def canonical_dict(self, *, include_calldata_hash: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if not include_calldata_hash:
            data.pop("calldataHash", None)
        return data

    def opportunity_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_dict(include_calldata_hash=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "0x" + hashlib.sha256(encoded).hexdigest()
