from __future__ import annotations

import asyncio
from dataclasses import dataclass

from web3 import Web3
from web3.providers.websocket import WebsocketProvider


@dataclass(frozen=True)
class WssCheckResult:
    url: str
    ok: bool
    latency_ms: float | None
    block: int | None
    error: str | None


async def _check(url: str, timeout: float = 5.0) -> WssCheckResult:
    try:
        w3 = Web3(WebsocketProvider(url, websocket_timeout=timeout))
        start = asyncio.get_event_loop().time()
        block = await asyncio.get_event_loop().run_in_executor(None, lambda: w3.eth.block_number)
        end = asyncio.get_event_loop().time()
        return WssCheckResult(url, True, (end - start) * 1000.0, int(block), None)
    except Exception as exc:
        return WssCheckResult(url, False, None, None, str(exc))


def check_wss_endpoints(urls: list[str]) -> list[WssCheckResult]:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        tasks = [_check(u) for u in urls]
        return loop.run_until_complete(asyncio.gather(*tasks))
    finally:
        loop.close()
