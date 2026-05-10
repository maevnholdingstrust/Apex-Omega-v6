from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CachedPoolState:
    pool: str
    payload: dict[str, Any]
    updated_at: float
    block_number: int | None = None


class PoolStateCache:
    """In-memory pool state cache with optional async Redis write-through.

    Synchronous path (``put`` / ``get`` / ``all``) never blocks — suitable for
    the hot path.  Async path (``put_async`` / ``get_async``) additionally
    persists to / hydrates from a ``RedisState`` instance when one is provided.
    """

    _REDIS_PREFIX = "apex:pool_state"

    def __init__(
        self,
        redis_state: Optional[Any] = None,
        redis_ttl_sec: int = 30,
    ) -> None:
        self._cache: dict[str, CachedPoolState] = {}
        self._redis = redis_state
        self._redis_ttl = redis_ttl_sec

    # ------------------------------------------------------------------
    # Synchronous API (no I/O)
    # ------------------------------------------------------------------

    def put(self, pool: str, payload: dict[str, Any], block_number: int | None = None) -> None:
        self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)

    def get(self, pool: str) -> Optional[CachedPoolState]:
        return self._cache.get(pool.lower())

    def all(self) -> list[CachedPoolState]:
        return list(self._cache.values())

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    def redis_key(self, pool: str) -> str:
        return f"{self._REDIS_PREFIX}:{pool.lower()}"

    # ------------------------------------------------------------------
    # Async API (Redis write-through / hydration)
    # ------------------------------------------------------------------

    async def put_async(
        self,
        pool: str,
        payload: dict[str, Any],
        block_number: int | None = None,
    ) -> None:
        """Write to in-memory cache and, if Redis is configured, write-through."""
        self.put(pool, payload, block_number)
        if self._redis is not None:
            key = self.redis_key(pool)
            record = {
                "pool": pool,
                "payload": payload,
                "updated_at": self._cache[pool.lower()].updated_at,
                "block_number": block_number,
            }
            await self._redis.set_json(key, record, ttl=self._redis_ttl)

    async def get_async(self, pool: str) -> Optional[CachedPoolState]:
        """Return from in-memory cache; hydrate from Redis on a miss."""
        entry = self.get(pool)
        if entry is not None:
            return entry
        if self._redis is None:
            return None
        key = self.redis_key(pool)
        record = await self._redis.get_json(key)
        if record is None:
            return None
        state = CachedPoolState(
            pool=record["pool"],
            payload=record["payload"],
            updated_at=float(record.get("updated_at", 0.0)),
            block_number=record.get("block_number"),
        )
        # Populate in-memory cache so subsequent sync reads also hit.
        self._cache[pool.lower()] = state
        return state

