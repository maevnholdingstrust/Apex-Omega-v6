from __future__ import annotations
import json, os
class RedisState:
    def __init__(self, url=None, prefix="apex"):
        self.url = url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.prefix = prefix
        self.client = None
    def enabled(self): return os.getenv("REDIS_ENABLED", "false").lower() == "true"
    async def connect(self):
        if not self.enabled(): return False
        try:
            import redis.asyncio as redis
            self.client = redis.from_url(self.url, decode_responses=True)
            await self.client.ping()
            return True
        except Exception:
            self.client = None
            return False
    def key(self, *parts): return ":".join([self.prefix, *map(str, parts)])
    async def set_json(self, key, value, ttl=None):
        if not self.client: return
        data = json.dumps(value, default=str)
        await self.client.set(key, data, ex=ttl) if ttl else await self.client.set(key, data)
    async def get_json(self, key):
        if not self.client: return None
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None
