
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReserveSnapshot:
    pool: str
    reserve0: int
    reserve1: int
    block_number: int | None = None


class MulticallReserveReader:
    """Batch reserve reader scaffold.

    Next implementation:
    - aggregate V2 getReserves()
    - aggregate V3 slot0()/liquidity()
    - return block-tagged snapshots
    """

    async def read_v2_reserves(self, pools: list[str]) -> list[ReserveSnapshot]:
        return []
