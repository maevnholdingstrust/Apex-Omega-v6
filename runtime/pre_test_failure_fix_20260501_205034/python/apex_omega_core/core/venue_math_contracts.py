from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MathModel(str, Enum):
    CPMM_XY_K = "cpmm_xy_k"
    UNISWAP_V3_TICK = "uniswap_v3_tick"
    ALGEBRA_TICK = "algebra_tick"
    CURVE_STABLESWAP = "curve_stableswap"
    BALANCER_WEIGHTED = "balancer_weighted"


@dataclass(frozen=True)
class VenueMathContract:
    kind: str
    model: MathModel
    required_fields: tuple[str, ...]
    execution_grade: bool
    notes: str


VENUE_MATH_CONTRACTS: dict[str, VenueMathContract] = {
    "v2": VenueMathContract(
        kind="v2",
        model=MathModel.CPMM_XY_K,
        required_fields=("reserve_in", "reserve_out", "fee_bps"),
        execution_grade=True,
        notes="Exact for UniswapV2-style constant product pools when reserves/direction are correct.",
    ),
    "v3": VenueMathContract(
        kind="v3",
        model=MathModel.UNISWAP_V3_TICK,
        required_fields=("sqrt_price_x96", "liquidity", "tick", "fee", "tick_bitmap_or_tick_lens"),
        execution_grade=False,
        notes="Spot approximation is not enough for large trades; execution-grade requires tick-walk liquidity.",
    ),
    "algebra": VenueMathContract(
        kind="algebra",
        model=MathModel.ALGEBRA_TICK,
        required_fields=("global_state", "liquidity", "tick", "tick_table"),
        execution_grade=False,
        notes="Requires Algebra-specific tick traversal before live sizing.",
    ),
    "curve": VenueMathContract(
        kind="curve",
        model=MathModel.CURVE_STABLESWAP,
        required_fields=("balances", "A", "fee", "i", "j"),
        execution_grade=False,
        notes="Requires pool-specific stableswap invariant math.",
    ),
    "balancer": VenueMathContract(
        kind="balancer",
        model=MathModel.BALANCER_WEIGHTED,
        required_fields=("balances", "weights", "swap_fee", "pool_id"),
        execution_grade=False,
        notes="Requires weighted invariant math and Vault pool metadata.",
    ),
}


def math_contract_for_kind(kind: str) -> VenueMathContract:
    if kind not in VENUE_MATH_CONTRACTS:
        raise ValueError(f"No math contract registered for venue kind {kind}")
    return VENUE_MATH_CONTRACTS[kind]


def assert_execution_grade(kind: str, state: dict[str, Any]) -> None:
    contract = math_contract_for_kind(kind)
    missing = [field for field in contract.required_fields if field not in state]
    if missing:
        raise ValueError(f"{kind} math state missing required fields: {missing}")
    if not contract.execution_grade:
        raise ValueError(f"{kind} math model is not execution-grade yet: {contract.notes}")
