from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

from .rpc_tester import get_w3
from .v3_tick_math import TickLiquidityNet

V3_TICK_ABI = [
    {"inputs": [{"name": "", "type": "int16"}], "name": "tickBitmap", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "", "type": "int24"}], "name": "ticks", "outputs": [
        {"name": "liquidityGross", "type": "uint128"},
        {"name": "liquidityNet", "type": "int128"},
        {"name": "feeGrowthOutside0X128", "type": "uint256"},
        {"name": "feeGrowthOutside1X128", "type": "uint256"},
        {"name": "tickCumulativeOutside", "type": "int56"},
        {"name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
        {"name": "secondsOutside", "type": "uint32"},
        {"name": "initialized", "type": "bool"},
    ], "stateMutability": "view", "type": "function"},
]


@dataclass(frozen=True)
class V3TickFetchResult:
    pool: str
    current_tick: int
    tick_spacing: int
    initialized_ticks: tuple[TickLiquidityNet, ...]
    execution_grade: bool
    reason: str


def _word_pos(tick: int, tick_spacing: int) -> int:
    compressed = tick // tick_spacing
    return compressed >> 8


def _tick_from_word_bit(word: int, bit: int, tick_spacing: int) -> int:
    compressed = (word << 8) + bit
    return compressed * tick_spacing


def fetch_initialized_ticks_around(
    pool_address: str,
    current_tick: int,
    tick_spacing: int,
    word_radius: int = 3,
) -> V3TickFetchResult:
    if tick_spacing <= 0:
        return V3TickFetchResult(pool_address, current_tick, tick_spacing, tuple(), False, "invalid tick spacing")
    w3 = get_w3()
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V3_TICK_ABI)
    center_word = _word_pos(current_tick, tick_spacing)
    ticks: list[TickLiquidityNet] = []
    for word in range(center_word - word_radius, center_word + word_radius + 1):
        bitmap = pool.functions.tickBitmap(word).call()
        if bitmap == 0:
            continue
        for bit in range(256):
            if (bitmap >> bit) & 1:
                tick_idx = _tick_from_word_bit(word, bit, tick_spacing)
                info = pool.functions.ticks(tick_idx).call()
                initialized = bool(info[7])
                liquidity_net = int(info[1])
                if initialized:
                    ticks.append(TickLiquidityNet(tick=tick_idx, liquidity_net=liquidity_net))
    ticks = sorted(set(ticks), key=lambda t: t.tick)
    ok = len(ticks) > 0
    return V3TickFetchResult(
        pool=pool_address,
        current_tick=current_tick,
        tick_spacing=tick_spacing,
        initialized_ticks=tuple(ticks),
        execution_grade=ok,
        reason="initialized ticks fetched" if ok else "no initialized ticks found around current tick",
    )
