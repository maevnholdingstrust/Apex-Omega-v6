
"""
Apex on-chain V2 pool discovery.

Primary purpose:
- replace DEXScreener as the main pool-discovery source
- use selected RPC endpoints from boot-time latency monitor
- query V2 factories directly:
    factory.getPair(tokenA, tokenB)
    pair.getReserves()
    pair.token0()
    pair.token1()

Policy:
- DEXScreener is optional enrichment only
- execution quotes must use on-chain reserves or verified pool state
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

import aiohttp


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Common Polygon V2 factory defaults. Existing repo config can override/extend these.
DEFAULT_V2_FACTORIES: dict[str, str] = {
    "quickswap": "0x5757371414417b8c6caad45baef941abc7d3ab32",
    "sushiswap": "0xc35dadb65012ec5796536bd9864ed8773abc74c4",
    "apeswap": "0xcf083be4164828f00cae704ec15a36d711491284",
    "dfyn": "0xe7fb3e833efe5f9c441105eb65ef8b261266423b",
    "jetswap": "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb",
}

# Function selectors.
SEL_GET_PAIR = "0xe6a43905"
SEL_GET_RESERVES = "0x0902f1ac"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"


@dataclass(frozen=True)
class OnchainV2Pool:
    dex_name: str
    factory: str
    pair_address: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    block_number: int | None = None


def _strip_0x(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _addr_word(address: str) -> str:
    raw = _strip_0x(address.lower())
    if len(raw) != 40:
        raise ValueError(f"invalid address: {address}")
    return raw.rjust(64, "0")


def encode_get_pair(token_a: str, token_b: str) -> str:
    return "0x" + _strip_0x(SEL_GET_PAIR) + _addr_word(token_a) + _addr_word(token_b)


def decode_address_word(result_hex: str) -> str | None:
    if not result_hex or result_hex == "0x":
        return None
    raw = _strip_0x(result_hex)
    if len(raw) < 64:
        return None
    addr = "0x" + raw[-40:]
    if addr.lower() == ZERO_ADDRESS.lower():
        return None
    return addr


def decode_uint256_word(word: str) -> int:
    return int(word, 16)


def decode_get_reserves(result_hex: str) -> tuple[int, int] | None:
    if not result_hex or result_hex == "0x":
        return None
    raw = _strip_0x(result_hex)
    if len(raw) < 64 * 3:
        return None
    reserve0 = decode_uint256_word(raw[0:64])
    reserve1 = decode_uint256_word(raw[64:128])
    return reserve0, reserve1


class RpcClient:
    def __init__(self, rpc_url: str, timeout_s: float = 8.0):
        self.rpc_url = rpc_url
        self.timeout_s = timeout_s
        self._id = 0

    async def call(self, session: aiohttp.ClientSession, to: str, data: str) -> str | None:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }

        try:
            async with session.post(self.rpc_url, json=payload, timeout=self.timeout_s) as resp:
                body = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    return None
                parsed = json.loads(body)
                if "error" in parsed:
                    return None
                return parsed.get("result")
        except Exception:
            return None

    async def block_number(self, session: aiohttp.ClientSession) -> int | None:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": "eth_blockNumber", "params": []}
        try:
            async with session.post(self.rpc_url, json=payload, timeout=self.timeout_s) as resp:
                parsed = json.loads(await resp.text())
                result = parsed.get("result")
                return int(result, 16) if isinstance(result, str) else None
        except Exception:
            return None


def _env_rpc_url() -> str:
    return (
        os.getenv("ACTIVE_DISCOVERY_RPC")
        or os.getenv("ACTIVE_EXECUTION_RPC")
        or os.getenv("POLYGON_RPC_URL")
        or os.getenv("WEB3_PROVIDER_URI")
        or os.getenv("PRIVATE_RPC_URL")
        or ""
    )


def _normalize_address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("0x") and len(value) == 42:
        return value
    return None


def extract_token_address(token: Any) -> str | None:
    if isinstance(token, str):
        return _normalize_address(token)

    for attr in ("address", "token_address", "contract_address"):
        if hasattr(token, attr):
            addr = _normalize_address(getattr(token, attr))
            if addr:
                return addr

    if isinstance(token, dict):
        for key in ("address", "token_address", "contract_address"):
            addr = _normalize_address(token.get(key))
            if addr:
                return addr

    return None


def normalize_factories(factories: dict[str, Any] | None = None) -> dict[str, str]:
    merged = dict(DEFAULT_V2_FACTORIES)
    if factories:
        for name, address in factories.items():
            addr = _normalize_address(str(address))
            if addr:
                merged[str(name).lower()] = addr
    return merged


async def discover_v2_pools_onchain(
    tokens: Iterable[Any],
    factories: dict[str, Any] | None = None,
    rpc_url: str | None = None,
    max_tokens: int | None = None,
    max_pairs: int | None = None,
    concurrency: int | None = None,
) -> list[OnchainV2Pool]:
    """Discover V2 pools directly from factories using eth_call."""
    rpc_url = rpc_url or _env_rpc_url()
    if not rpc_url:
        return []

    token_addresses: list[str] = []
    seen = set()

    for token in tokens:
        addr = extract_token_address(token)
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        token_addresses.append(addr)

    max_tokens = int(max_tokens or os.getenv("ONCHAIN_DISCOVERY_MAX_TOKENS", "80"))
    max_pairs = int(max_pairs or os.getenv("ONCHAIN_DISCOVERY_MAX_PAIRS", "2500"))
    concurrency = int(concurrency or os.getenv("ONCHAIN_DISCOVERY_CONCURRENCY", "32"))

    token_addresses = token_addresses[:max_tokens]
    token_pairs = list(combinations(token_addresses, 2))[:max_pairs]

    factory_map = normalize_factories(factories)
    client = RpcClient(rpc_url)
    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=client.timeout_s + 2.0)
    results: list[OnchainV2Pool] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        block_number = await client.block_number(session)

        async def check_pair(dex_name: str, factory: str, token_a: str, token_b: str):
            async with sem:
                pair_result = await client.call(session, factory, encode_get_pair(token_a, token_b))
                pair = decode_address_word(pair_result or "")
                if not pair:
                    return

                reserves_raw = await client.call(session, pair, SEL_GET_RESERVES)
                reserves = decode_get_reserves(reserves_raw or "")
                if not reserves:
                    return

                token0_raw = await client.call(session, pair, SEL_TOKEN0)
                token1_raw = await client.call(session, pair, SEL_TOKEN1)
                token0 = decode_address_word(token0_raw or "")
                token1 = decode_address_word(token1_raw or "")

                if not token0 or not token1:
                    token0, token1 = token_a, token_b

                reserve0, reserve1 = reserves
                if reserve0 <= 0 or reserve1 <= 0:
                    return

                results.append(
                    OnchainV2Pool(
                        dex_name=dex_name,
                        factory=factory,
                        pair_address=pair,
                        token0=token0,
                        token1=token1,
                        reserve0=reserve0,
                        reserve1=reserve1,
                        block_number=block_number,
                    )
                )

        tasks = [
            check_pair(dex_name, factory, token_a, token_b)
            for dex_name, factory in factory_map.items()
            for token_a, token_b in token_pairs
        ]

        await asyncio.gather(*tasks)

    return results
