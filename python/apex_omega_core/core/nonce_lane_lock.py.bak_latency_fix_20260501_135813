from __future__ import annotations
import asyncio
from collections import defaultdict
class NonceLaneLock:
    def __init__(self): self._locks = defaultdict(asyncio.Lock)
    def lock_for(self, wallet, lane_id="default"): return self._locks[f"{wallet.lower()}:{lane_id}"]
