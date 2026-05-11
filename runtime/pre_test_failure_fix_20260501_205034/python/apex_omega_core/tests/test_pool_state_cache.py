import pytest

from apex_omega_core.core.pool_state_cache import PoolStateCache


class FakeRedisState:
    def __init__(self):
        self.client = object()
        self.writes = {}

    def key(self, *parts):
        return ":".join(str(part) for part in parts)

    async def set_json(self, key, value, ttl=None):
        self.writes[key] = (value, ttl)

    async def get_json(self, key):
        stored = self.writes.get(key)
        return stored[0] if stored else None


def test_pool_state_cache_keeps_sync_memory_path():
    cache = PoolStateCache()
    cache.put("0xPool", {"reserve0": 1}, block_number=10)

    state = cache.get("0xpool")

    assert state is not None
    assert state.pool == "0xPool"
    assert state.payload == {"reserve0": 1}
    assert state.block_number == 10


@pytest.mark.asyncio
async def test_pool_state_cache_writes_through_to_redis():
    redis_state = FakeRedisState()
    cache = PoolStateCache(redis_state=redis_state, redis_ttl_sec=7)

    await cache.put_async("0xPool", {"reserve0": 1}, block_number=10)

    key = cache.redis_key("0xPool")
    assert redis_state.writes[key][1] == 7
    assert redis_state.writes[key][0]["payload"] == {"reserve0": 1}


@pytest.mark.asyncio
async def test_pool_state_cache_hydrates_from_redis_on_miss():
    redis_state = FakeRedisState()
    writer = PoolStateCache(redis_state=redis_state, redis_ttl_sec=7)
    reader = PoolStateCache(redis_state=redis_state, redis_ttl_sec=7)
    await writer.put_async("0xPool", {"reserve0": 1}, block_number=10)

    state = await reader.get_async("0xPOOL")

    assert state is not None
    assert state.pool == "0xPool"
    assert state.payload == {"reserve0": 1}
    assert state.block_number == 10
