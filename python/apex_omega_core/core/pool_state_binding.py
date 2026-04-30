from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3

from .polygon_market_registry import TOKENS
from .rpc_tester import V2_PAIR_ABI, V3_POOL_ABI, get_w3
from .venue_math_contracts import assert_execution_grade, math_contract_for_kind


@dataclass(frozen=True)
class BoundPoolState:
    kind: str
    pool: str
    token_in: str
    token_out: str
    state: dict[str, Any]
    execution_grade: bool
    reason: str


def _token_decimals(symbol: str) -> int:
    return TOKENS[symbol].decimals


def bind_v2_pool_state(pool: str, token_in_symbol: str, token_out_symbol: str, fee_bps: float) -> BoundPoolState:
    w3 = get_w3()
    pair = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=V2_PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    token0 = pair.functions.token0().call()
    token1 = pair.functions.token1().call()
    token_in = TOKENS[token_in_symbol].address.lower()
    token_out = TOKENS[token_out_symbol].address.lower()
    if token0.lower() == token_in and token1.lower() == token_out:
        reserve_in = r0 / (10 ** TOKENS[token_in_symbol].decimals)
        reserve_out = r1 / (10 ** TOKENS[token_out_symbol].decimals)
    elif token1.lower() == token_in and token0.lower() == token_out:
        reserve_in = r1 / (10 ** TOKENS[token_in_symbol].decimals)
        reserve_out = r0 / (10 ** TOKENS[token_out_symbol].decimals)
    else:
        return BoundPoolState("v2", pool, token_in_symbol, token_out_symbol, {}, False, "pool token mismatch")
    state = {"reserve_in": reserve_in, "reserve_out": reserve_out, "fee_bps": fee_bps}
    try:
        assert_execution_grade("v2", state)
        return BoundPoolState("v2", pool, token_in_symbol, token_out_symbol, state, True, "v2 reserves bound")
    except Exception as exc:
        return BoundPoolState("v2", pool, token_in_symbol, token_out_symbol, state, False, str(exc))


def bind_v3_pool_state(pool: str, token_in_symbol: str, token_out_symbol: str, fee: int) -> BoundPoolState:
    w3 = get_w3()
    p = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=V3_POOL_ABI)
    slot0 = p.functions.slot0().call()
    liquidity = p.functions.liquidity().call()
    token0 = p.functions.token0().call()
    token1 = p.functions.token1().call()
    state = {
        "sqrt_price_x96": slot0[0],
        "tick": slot0[1],
        "liquidity": liquidity,
        "fee": fee,
        "token0": token0,
        "token1": token1,
    }
    try:
        assert_execution_grade("v3", state)
        return BoundPoolState("v3", pool, token_in_symbol, token_out_symbol, state, True, "v3 tick state bound")
    except Exception as exc:
        return BoundPoolState("v3", pool, token_in_symbol, token_out_symbol, state, False, str(exc))


def require_bound_execution_grade(bound_states: list[BoundPoolState]) -> None:
    bad = [b for b in bound_states if not b.execution_grade]
    if bad:
        details = "; ".join(f"{b.kind}:{b.pool}:{b.reason}" for b in bad)
        raise ValueError(f"route has non-execution-grade pool state: {details}")
