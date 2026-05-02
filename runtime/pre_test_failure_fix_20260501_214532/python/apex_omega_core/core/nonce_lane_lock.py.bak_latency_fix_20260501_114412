
from __future__ import annotations

import asyncio
from collections import defaultdict


class NonceLaneLock:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock_for(self, wallet: str, lane_id: str = "default") -> asyncio.Lock:
        return self._locks[f"{wallet.lower()}:{lane_id}"]
