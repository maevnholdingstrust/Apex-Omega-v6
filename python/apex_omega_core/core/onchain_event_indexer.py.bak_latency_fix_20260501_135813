from __future__ import annotations
import os
class OnchainEventIndexer:
    def __init__(self, wss_url=None):
        self.wss_url = wss_url or os.getenv("ACTIVE_DISCOVERY_WSS") or os.getenv("POLYGON_WSS_ACTIVE")
    async def run(self): raise NotImplementedError("WSS event loop not implemented yet")
