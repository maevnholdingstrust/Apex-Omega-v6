
from __future__ import annotations

import json
import os
from typing import Any


class RedisState:
    def __init__(self, url: str | None = None, prefix: str = "apex"):
        self.url = url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.prefix = prefix
        self.client = None

    def enabled(self) -> bool:
        return os.getenv("REDIS_ENABLED", "false").lower() == "true"

    async def connect(self) -> bool:
        if not self.enabled():
            return False
        try:
            import redis.asyncio as redis
            self.client = redis.from_url(self.url, decode_responses=True)
            await self.client.ping()
            return True
        except Exception:
            self.client = None
            return False

    async def close(self) -> None:
        if not self.client:
            return
        close = getattr(self.client, "aclose", None) or getattr(self.client, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result
        self.client = None

    def key(self, *parts: str) -> str:
        return ":".join([self.prefix, *[str(p) for p in parts]])

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not self.client:
            return
        data = json.dumps(value, default=str)
        if ttl:
            await self.client.set(key, data, ex=ttl)
        else:
            await self.client.set(key, data)

    async def get_json(self, key: str) -> Any | None:
        if not self.client:
            return None
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None
