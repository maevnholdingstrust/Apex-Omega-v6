
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CachedPoolState:
    pool: str
    payload: dict[str, Any]
    updated_at: float
    block_number: int | None = None


class PoolStateCache:
    def __init__(self):
        self._cache: dict[str, CachedPoolState] = {}

    def put(self, pool: str, payload: dict[str, Any], block_number: int | None = None) -> None:
        self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)

    def get(self, pool: str) -> CachedPoolState | None:
        return self._cache.get(pool.lower())

    def all(self) -> list[CachedPoolState]:
        return list(self._cache.values())
