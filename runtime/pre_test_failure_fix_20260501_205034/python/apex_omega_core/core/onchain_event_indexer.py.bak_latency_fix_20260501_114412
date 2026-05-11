
from __future__ import annotations

import os


class OnchainEventIndexer:
    """WSS event indexer scaffold.

    Target events:
    - PairCreated / PoolCreated
    - Sync
    - Swap
    """

    def __init__(self, wss_url: str | None = None):
        self.wss_url = wss_url or os.getenv("ACTIVE_DISCOVERY_WSS") or os.getenv("POLYGON_WSS_ACTIVE")

    async def run(self) -> None:
        raise NotImplementedError("WSS event loop not implemented yet")
