"""Balancer V3 Vault transient-accounting adapter.

Balancer V3 Vault execution is not a Uniswap-style router call.  The Vault must
be entered through ``unlock(bytes)`` and all debt/credit deltas created inside
the callback must be settled before the transient state closes.

This module only builds/validates calldata.  It intentionally does not invent
pool math, quote outputs, settlement hints, or callback payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, List

from eth_abi import encode
from web3 import Web3

from .executable_payloads import VmStep


BALANCER_V3_UNLOCK_SIG = "unlock(bytes)"
BALANCER_V3_SETTLE_SIG = "settle(address,uint256)"
BALANCER_V3_SEND_TO_SIG = "sendTo(address,address,uint256)"
BALANCER_V3_SWAP_SIG = "swap((uint8,address,address,address,uint256,uint256,bytes))"


class BalancerSwapKind(IntEnum):
    EXACT_IN = 0
    EXACT_OUT = 1


@dataclass(frozen=True)
class BalancerV3VaultSwap:
    pool: str
    token_in: str
    token_out: str
    amount_given_raw: int
    limit_raw: int
    kind: BalancerSwapKind = BalancerSwapKind.EXACT_IN
    user_data: bytes = b""

    def validate(self) -> None:
        _addr(self.pool)
        _addr(self.token_in)
        _addr(self.token_out)
        if int(self.amount_given_raw) <= 0:
            raise ValueError("amount_given_raw must be positive")
        if int(self.limit_raw) <= 0:
            raise ValueError("limit_raw must be positive")
        if int(self.kind) not in (0, 1):
            raise ValueError("Balancer swap kind must be EXACT_IN(0) or EXACT_OUT(1)")


@dataclass(frozen=True)
class BalancerV3Settlement:
    token: str
    amount_hint: int

    def validate(self) -> None:
        _addr(self.token)
        if int(self.amount_hint) <= 0:
            raise ValueError("settlement amount_hint must be positive")


@dataclass(frozen=True)
class BalancerV3UnlockPlan:
    vault: str
    callback_data: bytes
    token_in: str
    token_out: str
    amount_in: int
    min_amount_out: int
    settlements: List[BalancerV3Settlement] = field(default_factory=list)
    swaps: List[BalancerV3VaultSwap] = field(default_factory=list)
    call_value: int = 0

    def validate(self) -> None:
        _addr(self.vault)
        _addr(self.token_in)
        _addr(self.token_out)
        if not isinstance(self.callback_data, (bytes, bytearray)) or len(self.callback_data) == 0:
            raise ValueError("Balancer unlock callback_data must be non-empty bytes")
        if int(self.amount_in) <= 0:
            raise ValueError("amount_in must be positive")
        if int(self.min_amount_out) <= 0:
            raise ValueError("min_amount_out must be positive")
        if int(self.call_value) < 0:
            raise ValueError("call_value cannot be negative")
        for settlement in self.settlements:
            settlement.validate()
        for swap in self.swaps:
            swap.validate()
        validate_balancer_unlock_settle_plan(self)


def encode_balancer_v3_unlock(callback_data: bytes) -> str:
    if not isinstance(callback_data, (bytes, bytearray)) or len(callback_data) == 0:
        raise ValueError("callback_data must be non-empty bytes")
    return Web3.to_hex(_selector(BALANCER_V3_UNLOCK_SIG) + encode(["bytes"], [bytes(callback_data)]))


def encode_balancer_v3_settle(token: str, amount_hint: int) -> str:
    settlement = BalancerV3Settlement(token=token, amount_hint=int(amount_hint))
    settlement.validate()
    return Web3.to_hex(_selector(BALANCER_V3_SETTLE_SIG) + encode(["address", "uint256"], [_addr(token), int(amount_hint)]))


def encode_balancer_v3_send_to(token: str, to: str, amount: int) -> str:
    if int(amount) <= 0:
        raise ValueError("sendTo amount must be positive")
    return Web3.to_hex(_selector(BALANCER_V3_SEND_TO_SIG) + encode(["address", "address", "uint256"], [_addr(token), _addr(to), int(amount)]))


def encode_balancer_v3_swap(swap: BalancerV3VaultSwap) -> str:
    swap.validate()
    params = (
        int(swap.kind),
        _addr(swap.pool),
        _addr(swap.token_in),
        _addr(swap.token_out),
        int(swap.amount_given_raw),
        int(swap.limit_raw),
        bytes(swap.user_data),
    )
    return Web3.to_hex(_selector(BALANCER_V3_SWAP_SIG) + encode(["(uint8,address,address,address,uint256,uint256,bytes)"], [params]))


def build_balancer_v3_unlock_step(plan: BalancerV3UnlockPlan) -> VmStep:
    plan.validate()
    step = VmStep(
        venue=_addr(plan.vault),
        tokenIn=_addr(plan.token_in),
        tokenOut=_addr(plan.token_out),
        amountIn=int(plan.amount_in),
        minAmountOut=int(plan.min_amount_out),
        callValue=int(plan.call_value),
        payload=encode_balancer_v3_unlock(plan.callback_data),
    )
    step.validate()
    return step


def validate_balancer_unlock_settle_plan(plan: BalancerV3UnlockPlan) -> None:
    """Fail closed unless the plan explicitly acknowledges settlement.

    The Vault itself enforces non-zero delta settlement at runtime.  Off-chain we
    cannot prove the callback's internal deltas without simulation, but we can
    prevent a Balancer V3 step from being classified as executable unless its
    transient-accounting plan includes settlement hints for every token touched
    by the declared swap operations.
    """
    touched = {_addr(plan.token_in).lower(), _addr(plan.token_out).lower()}
    for swap in plan.swaps:
        touched.add(_addr(swap.token_in).lower())
        touched.add(_addr(swap.token_out).lower())

    settled = {_addr(s.token).lower() for s in plan.settlements}
    missing = sorted(touched - settled)
    if missing:
        raise ValueError(f"Balancer V3 unlock plan missing settlement hints for tokens: {missing}")


def build_settle_payloads(settlements: Iterable[BalancerV3Settlement]) -> list[str]:
    payloads: list[str] = []
    for settlement in settlements:
        settlement.validate()
        payloads.append(encode_balancer_v3_settle(settlement.token, settlement.amount_hint))
    return payloads


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _addr(value: str) -> str:
    if not isinstance(value, str) or not Web3.is_address(value):
        raise ValueError(f"invalid EVM address: {value!r}")
    return Web3.to_checksum_address(value)
