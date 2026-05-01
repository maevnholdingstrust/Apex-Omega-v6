
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from .redis_state import RedisState


@dataclass
class CachedPoolState:
    pool: str
    payload: dict[str, Any]
    updated_at: float
    block_number: int | None = None


class PoolStateCache:
    def __init__(
        self,
        redis_state: RedisState | None = None,
        *,
        redis_ttl_sec: int = 3,
        namespace: str = "pool_state",
    ):
        self._cache: dict[str, CachedPoolState] = {}
        self.redis_state = redis_state
        self.redis_ttl_sec = int(redis_ttl_sec)
        self.namespace = namespace

    def put(self, pool: str, payload: dict[str, Any], block_number: int | None = None) -> None:
        self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)

    def get(self, pool: str) -> CachedPoolState | None:
        return self._cache.get(pool.lower())

    def all(self) -> list[CachedPoolState]:
        return list(self._cache.values())

    def redis_key(self, pool: str) -> str:
        redis_state = self.redis_state or RedisState()
        return redis_state.key(self.namespace, pool.lower())

    async def connect_redis(self) -> bool:
        if self.redis_state is None:
            self.redis_state = RedisState()
        return await self.redis_state.connect()

    async def put_async(
        self,
        pool: str,
        payload: dict[str, Any],
        block_number: int | None = None,
    ) -> None:
        self.put(pool, payload, block_number)
        if not self.redis_state or not self.redis_state.client:
            return
        state = self._cache[pool.lower()]
        await self.redis_state.set_json(
            self.redis_key(pool),
            asdict(state),
            ttl=self.redis_ttl_sec,
        )

    async def get_async(self, pool: str) -> CachedPoolState | None:
        local = self.get(pool)
        if local is not None:
            return local
        if not self.redis_state or not self.redis_state.client:
            return None
        raw = await self.redis_state.get_json(self.redis_key(pool))
        if not raw:
            return None
        state = CachedPoolState(
            pool=str(raw["pool"]),
            payload=dict(raw.get("payload") or {}),
            updated_at=float(raw["updated_at"]),
            block_number=raw.get("block_number"),
        )
        self._cache[pool.lower()] = state
        return state
