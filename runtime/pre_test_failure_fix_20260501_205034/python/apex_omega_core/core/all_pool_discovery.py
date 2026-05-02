
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import aiohttp


ZERO = "0x0000000000000000000000000000000000000000"

class PoolFamily(str, Enum):
    V2_CPMM = "v2_cpmm"
    V3_CLMM = "v3_clmm"
    ALGEBRA_CLMM = "algebra_clmm"
    CURVE_STABLE = "curve_stable"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiscoveredPool:
    chain_id: int
    family: str
    dex_name: str
    factory_or_vault: str | None
    pool_address: str
    token0: str | None = None
    token1: str | None = None
    fee_tier: int | None = None
    fee_bps: int | None = None
    reserve0: int | None = None
    reserve1: int | None = None
    sqrt_price_x96: int | None = None
    tick: int | None = None
    liquidity: int | None = None
    pool_id: str | None = None
    tokens: list[str] | None = None
    balances: list[str] | None = None
    execution_supported: bool = False
    math_mode: str = "unknown"
    source: str = "all_pool_discovery"


V2_FACTORIES = {
    "quickswap_v2": "0x5757371414417b8c6caad45baef941abc7d3ab32",
    "sushiswap_v2": "0xc35dadb65012ec5796536bd9864ed8773abc74c4",
    "apeswap_v2": "0xcf083be4164828f00cae704ec15a36d711491284",
    "dfyn_v2": "0xe7fb3e833efe5f9c441105eb65ef8b261266423b",
    "jetswap_v2": "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb",
}

# Known/default candidates. Add verified factories as needed.
V3_FACTORIES = {
    "uniswap_v3": "0x1f98431c8ad98523631ae4a59f267346ea31f984",
}

ALGEBRA_FACTORIES = {
    # QuickSwap Algebra factory can be set through .env if your repo uses a verified address.
    # QUICKSWAP_ALGEBRA_FACTORY=0x...
}

V3_FEE_TIERS = [100, 500, 3000, 10000]

# Balancer V2 Vault on Polygon.
BALANCER_VAULT = os.getenv("BALANCER_VAULT", "0xba12222222228d8ba445958a75a0704d566bf2c8")

# Curve is registry/factory-dependent. Keep scaffold discovery-disabled unless addresses are supplied.
CURVE_REGISTRIES = {
    k.replace("CURVE_REGISTRY_", "").lower(): v
    for k, v in os.environ.items()
    if k.startswith("CURVE_REGISTRY_") and v.startswith("0x")
}


SEL_GET_PAIR = "0xe6a43905"
SEL_GET_RESERVES = "0x0902f1ac"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"

# Uniswap V3 factory getPool(address,address,uint24)
SEL_GET_POOL = "0x1698ee82"
SEL_SLOT0 = "0x3850c7bd"
SEL_LIQUIDITY = "0x1a686502"
SEL_FEE = "0xddca3f43"
SEL_TICK_SPACING = "0xd0c93a7c"


def _strip(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _addr_word(address: str) -> str:
    raw = _strip(address.lower())
    if len(raw) != 40:
        raise ValueError(f"invalid address: {address}")
    return raw.rjust(64, "0")


def _uint24_word(value: int) -> str:
    return hex(value)[2:].rjust(64, "0")


def encode_get_pair(a: str, b: str) -> str:
    return "0x" + _strip(SEL_GET_PAIR) + _addr_word(a) + _addr_word(b)


def encode_get_pool(a: str, b: str, fee: int) -> str:
    return "0x" + _strip(SEL_GET_POOL) + _addr_word(a) + _addr_word(b) + _uint24_word(fee)


def decode_address(result: str | None) -> str | None:
    if not result or result == "0x":
        return None
    raw = _strip(result)
    if len(raw) < 64:
        return None
    addr = "0x" + raw[-40:]
    if addr.lower() == ZERO:
        return None
    return addr


def decode_reserves(result: str | None) -> tuple[int, int] | None:
    if not result or result == "0x":
        return None
    raw = _strip(result)
    if len(raw) < 192:
        return None
    return int(raw[0:64], 16), int(raw[64:128], 16)


def decode_slot0(result: str | None) -> tuple[int, int] | None:
    if not result or result == "0x":
        return None
    raw = _strip(result)
    if len(raw) < 128:
        return None
    sqrt_price_x96 = int(raw[0:64], 16)
    tick_raw = int(raw[64:128], 16)
    # int24 sign conversion
    if tick_raw >= 2 ** 23:
        tick_raw -= 2 ** 24
    return sqrt_price_x96, tick_raw


def decode_uint(result: str | None) -> int | None:
    if not result or result == "0x":
        return None
    raw = _strip(result)
    if len(raw) < 64:
        return None
    return int(raw[-64:], 16)


def normalize_addr(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
        return value
    if isinstance(value, dict):
        for key in ("address", "token_address", "contract_address"):
            v = value.get(key)
            if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
                return v
    for attr in ("address", "token_address", "contract_address"):
        if hasattr(value, attr):
            v = getattr(value, attr)
            if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
                return v
    return None


def rpc_url() -> str:
    return (
        os.getenv("ACTIVE_DISCOVERY_RPC")
        or os.getenv("ACTIVE_EXECUTION_RPC")
        or os.getenv("POLYGON_RPC_URL")
        or os.getenv("WEB3_PROVIDER_URI")
        or os.getenv("PRIVATE_RPC_URL")
        or ""
    )


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self.i = 0

    async def call(self, session: aiohttp.ClientSession, to: str, data: str) -> str | None:
        self.i += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.i,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }
        try:
            async with session.post(self.url, json=payload, timeout=8) as resp:
                if resp.status < 200 or resp.status >= 300:
                    return None
                parsed = json.loads(await resp.text())
                if "error" in parsed:
                    return None
                return parsed.get("result")
        except Exception:
            return None




async def _gather_with_progress(tasks, label: str, progress_every: int = 500):
    total = len(tasks)
    if total == 0:
        return []
    print(f"[{label}] tasks={total}")
    results = []
    done = 0
    for fut in asyncio.as_completed(tasks):
        try:
            results.append(await fut)
        except Exception:
            results.append(None)
        done += 1
        if done % progress_every == 0 or done == total:
            print(f"[{label}] progress={done}/{total}")
    return results


async def discover_v2(tokens: list[str], rpc: Rpc, session: aiohttp.ClientSession, max_pairs: int, concurrency: int) -> list[DiscoveredPool]:
    sem = asyncio.Semaphore(concurrency)
    pairs = list(combinations(tokens, 2))[:max_pairs]
    out: list[DiscoveredPool] = []

    async def one(dex: str, factory: str, a: str, b: str):
        async with sem:
            pair = decode_address(await rpc.call(session, factory, encode_get_pair(a, b)))
            if not pair:
                return

            reserves = decode_reserves(await rpc.call(session, pair, SEL_GET_RESERVES))
            if not reserves:
                return

            t0 = decode_address(await rpc.call(session, pair, SEL_TOKEN0)) or a
            t1 = decode_address(await rpc.call(session, pair, SEL_TOKEN1)) or b
            r0, r1 = reserves
            if r0 <= 0 or r1 <= 0:
                return

            out.append(DiscoveredPool(
                chain_id=137,
                family=PoolFamily.V2_CPMM.value,
                dex_name=dex,
                factory_or_vault=factory,
                pool_address=pair,
                token0=t0,
                token1=t1,
                reserve0=r0,
                reserve1=r1,
                fee_bps=30,
                execution_supported=True,
                math_mode="reserve_cpmm",
            ))

    tasks = [
        one(dex, factory, a, b)
        for dex, factory in V2_FACTORIES.items()
        for a, b in pairs
    ]
    await _gather_with_progress(tasks, "V2_DISCOVERY", progress_every=500)
    return out


async def discover_v3(tokens: list[str], rpc: Rpc, session: aiohttp.ClientSession, max_pairs: int, concurrency: int) -> list[DiscoveredPool]:
    sem = asyncio.Semaphore(concurrency)
    pairs = list(combinations(tokens, 2))[:max_pairs]
    out: list[DiscoveredPool] = []

    factories = dict(V3_FACTORIES)
    if os.getenv("QUICKSWAP_ALGEBRA_FACTORY"):
        factories["quickswap_algebra"] = os.getenv("QUICKSWAP_ALGEBRA_FACTORY")

    async def one(dex: str, factory: str, a: str, b: str, fee: int):
        async with sem:
            pool = decode_address(await rpc.call(session, factory, encode_get_pool(a, b, fee)))
            if not pool:
                return

            slot0 = decode_slot0(await rpc.call(session, pool, SEL_SLOT0))
            liquidity = decode_uint(await rpc.call(session, pool, SEL_LIQUIDITY))
            if not slot0 or not liquidity or liquidity <= 0:
                return

            sqrt_price_x96, tick = slot0
            family = PoolFamily.ALGEBRA_CLMM.value if "algebra" in dex else PoolFamily.V3_CLMM.value
            math_mode = "algebra_tick_clmm" if "algebra" in dex else "tick_clmm"

            out.append(DiscoveredPool(
                chain_id=137,
                family=family,
                dex_name=dex,
                factory_or_vault=factory,
                pool_address=pool,
                token0=a,
                token1=b,
                fee_tier=fee,
                fee_bps=fee // 100,
                sqrt_price_x96=sqrt_price_x96,
                tick=tick,
                liquidity=liquidity,
                execution_supported=False,
                math_mode=math_mode,
            ))

    tasks = [
        one(dex, factory, a, b, fee)
        for dex, factory in factories.items()
        for a, b in pairs
        for fee in V3_FEE_TIERS
    ]
    await _gather_with_progress(tasks, "V3_DISCOVERY", progress_every=500)
    return out


async def discover_curve_scaffold() -> list[DiscoveredPool]:
    # Curve needs registry/factory-specific enumeration. This scaffold records that
    # the family is discoverable but not executable until registry addresses are supplied.
    return []


async def discover_balancer_scaffold() -> list[DiscoveredPool]:
    # Full Balancer discovery requires querying pool IDs from subgraph/events or a maintained registry,
    # then Vault.getPoolTokens(poolId). Keep scaffold execution-blocked.
    return []


async def discover_all_pools(tokens: Iterable[Any]) -> list[DiscoveredPool]:
    url = rpc_url()
    if not url:
        return []

    token_addrs = []
    seen = set()
    for token in tokens:
        addr = normalize_addr(token)
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        token_addrs.append(addr)

    max_tokens = int(os.getenv("ALL_POOLS_MAX_TOKENS", os.getenv("ONCHAIN_DISCOVERY_MAX_TOKENS", "100")))
    max_pairs = int(os.getenv("ALL_POOLS_MAX_PAIRS", os.getenv("ONCHAIN_DISCOVERY_MAX_PAIRS", "5000")))
    concurrency = int(os.getenv("ALL_POOLS_CONCURRENCY", os.getenv("ONCHAIN_DISCOVERY_CONCURRENCY", "48")))

    token_addrs = token_addrs[:max_tokens]
    rpc = Rpc(url)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        enabled = {x.strip().lower() for x in os.getenv("DISCOVER_POOL_FAMILIES", "v2,v3,algebra,curve,balancer").split(",")}

        tasks = []
        if "v2" in enabled:
            tasks.append(discover_v2(token_addrs, rpc, session, max_pairs=max_pairs, concurrency=concurrency))
        if "v3" in enabled or "algebra" in enabled:
            tasks.append(discover_v3(token_addrs, rpc, session, max_pairs=max_pairs, concurrency=concurrency))
        if "curve" in enabled:
            tasks.append(discover_curve_scaffold())
        if "balancer" in enabled:
            tasks.append(discover_balancer_scaffold())

        chunks = await asyncio.gather(*tasks)
        pools = [p for chunk in chunks for p in chunk]

    return pools


def save_pool_report(pools: list[DiscoveredPool], out_dir: str | Path = "runtime") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = [asdict(p) for p in pools]
    (out / "all_discovered_pools.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for p in pools:
        counts[p.family] = counts.get(p.family, 0) + 1
    (out / "all_discovered_pool_counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
