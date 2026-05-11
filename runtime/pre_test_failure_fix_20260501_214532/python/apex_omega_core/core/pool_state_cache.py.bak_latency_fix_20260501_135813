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
    def __init__(self): self._cache = {}
    def put(self, pool, payload, block_number=None): self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)
    def get(self, pool): return self._cache.get(pool.lower())
    def all(self): return list(self._cache.values())
