
from __future__ import annotations

from dataclasses import dataclass
from math import pow

BALANCER_V3_POLYGON_VAULT = "0xbA1333333333a1BA1108E8412f11850A5C319bA9"
BALANCER_V3_FLASH_FEE_BPS = 0

@dataclass(frozen=True)
class BalancerV3PoolState:
    pool: str
    pool_type: str
    tokens: list[str]
    balances_raw: list[int]
    last_live_balances: list[int]
    weights: list[float] | None
    swap_fee_bps: int
    vault: str = BALANCER_V3_POLYGON_VAULT

def validate_balancer_state(state: BalancerV3PoolState) -> tuple[bool, str]:
    if state.vault.lower() != BALANCER_V3_POLYGON_VAULT.lower():
        return False, "wrong_vault"
    if len(state.tokens) < 2:
        return False, "not_enough_tokens"
    if len(state.last_live_balances) != len(state.tokens):
        return False, "live_balance_token_length_mismatch"
    if any(int(x) <= 0 for x in state.last_live_balances):
        return False, "zero_live_balance"
    if state.swap_fee_bps < 0:
        return False, "invalid_swap_fee"
    if state.pool_type.upper() == "WEIGHTED":
        if not state.weights or len(state.weights) != len(state.tokens):
            return False, "missing_weights"
    return True, "ok"

def weighted_amount_out(
    amount_in: float,
    balance_in: float,
    weight_in: float,
    balance_out: float,
    weight_out: float,
    fee_bps: int,
) -> float:
    amount_after_fee = amount_in * (1.0 - fee_bps / 10_000.0)
    ratio = balance_in / (balance_in + amount_after_fee)
    return balance_out * (1.0 - pow(ratio, weight_in / weight_out))

def quote_balancer_weighted_guarded(*, state: BalancerV3PoolState, token_in_index: int, token_out_index: int, amount_in: float) -> float:
    ok, reason = validate_balancer_state(state)
    if not ok:
        raise ValueError(f"Balancer V3 quote rejected: {reason}")
    if state.pool_type.upper() != "WEIGHTED":
        raise NotImplementedError("Only Balancer weighted guard is installed; stable/composable stable remains gated")
    return weighted_amount_out(
        amount_in=amount_in,
        balance_in=float(state.last_live_balances[token_in_index]),
        weight_in=float(state.weights[token_in_index]),
        balance_out=float(state.last_live_balances[token_out_index]),
        weight_out=float(state.weights[token_out_index]),
        fee_bps=state.swap_fee_bps,
    )

def balancer_flash_fee_bps() -> int:
    return BALANCER_V3_FLASH_FEE_BPS
