from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys
import re

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
PY = ROOT / "python"
CORE = PY / "apex_omega_core" / "core"
BOT = PY / "polygon_arbitrage_bot.py"
ARB = CORE / "polygon_arbitrage.py"
ENV = ROOT / ".env"

def backup(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak_low_latency_{STAMP}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] {path} -> {bak}")

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup(path)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"[WRITE] {path}")

def compile_py(path: Path):
    if not path.exists():
        return
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[FAIL] py_compile {path}")
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)
    print(f"[OK] compiled {path}")

def set_env(text: str, key: str, value: str) -> str:
    rx = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if rx.search(text):
        return rx.sub(line, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"

# --------------------------------------------------------------------
# 1. Force-fix on-chain Pool converter
# --------------------------------------------------------------------
if ARB.exists():
    backup(ARB)
    text = ARB.read_text(encoding="utf-8", errors="replace")

    if "import inspect" not in text:
        if "import os" in text:
            text = text.replace("import os", "import os\nimport inspect", 1)
        else:
            text = "import inspect\n" + text
        print("[PATCH] Added import inspect")

    if "from .pool_math_registry import classify_pool_kwargs" not in text:
        marker = "from .onchain_v2_discovery import"
        if marker in text:
            text = text.replace(
                marker,
                "from .pool_math_registry import classify_pool_kwargs\n" + marker,
                1,
            )
        else:
            text = "from .pool_math_registry import classify_pool_kwargs\n" + text
        print("[PATCH] Added pool_math_registry import")

    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("    def _pool_from_onchain_v2("):
            start = i
            break

    if start is not None:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("    def ") or lines[j].startswith("    async def "):
                end = j
                break

        new_func = r'''    def _pool_from_onchain_v2(self, raw: OnchainV2Pool) -> Pool:
        """Convert on-chain V2 discovery result into the repo Pool model.

        Constructor-safe:
        - only passes fields accepted by the actual Pool constructor
        - attaches extra dynamic math metadata after construction
        - prevents fee_bps/pool_type constructor crashes
        """
        reserve0 = float(raw.reserve0)
        reserve1 = float(raw.reserve1)

        classified = classify_pool_kwargs(
            chain_id=137,
            dex_name=raw.dex_name,
            factory_address=raw.factory,
            pool_address=raw.pair_address,
            token0=raw.token0,
            token1=raw.token1,
            reserve0=raw.reserve0,
            reserve1=raw.reserve1,
            source="onchain_v2",
        )

        meta = {
            "address": raw.pair_address,
            "pool_address": raw.pair_address,
            "pair_address": raw.pair_address,
            "dex": classified.dex_name,
            "dex_name": classified.dex_name,
            "token0": raw.token0,
            "token1": raw.token1,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "reserves0": reserve0,
            "reserves1": reserve1,
            "tvl_usd": 0.0,
            "liquidity_usd": 0.0,
            "fee": (classified.fee_bps or 30) / 10_000,
            "fee_bps": classified.fee_bps or 30,
            "fee_tier": classified.fee_tier,
            "pool_type": classified.pool_family.value,
            "math_mode": classified.math_mode.value,
            "router_type": classified.router_type,
            "quote_engine": classified.quote_engine,
            "calldata_engine": classified.calldata_engine,
            "execution_supported": classified.execution_supported,
            "source": classified.source,
        }

        sig = inspect.signature(Pool)
        allowed = set(sig.parameters.keys())
        kwargs = {k: v for k, v in meta.items() if k in allowed}

        try:
            pool = Pool(**kwargs)
        except TypeError:
            fallback_key_sets = (
                ("address", "dex", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("address", "dex", "token0", "token1", "reserve0", "reserve1"),
                ("pool_address", "dex_name", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("pair_address", "dex_name", "token0", "token1", "reserve0", "reserve1"),
            )
            last_error = None
            pool = None
            for keys in fallback_key_sets:
                try:
                    candidate_kwargs = {k: meta[k] for k in keys if k in allowed}
                    pool = Pool(**candidate_kwargs)
                    break
                except TypeError as exc:
                    last_error = exc
            if pool is None:
                raise last_error or TypeError("Unable to construct Pool from on-chain V2 metadata")

        for k, v in meta.items():
            try:
                setattr(pool, k, v)
            except Exception:
                pass

        return pool
'''
        lines = lines[:start] + new_func.splitlines() + [""] + lines[end:]
        text = "\n".join(lines) + "\n"
        print("[PATCH] Force-replaced _pool_from_onchain_v2")
    else:
        print("[WARN] _pool_from_onchain_v2 not found; skipping converter patch")

    ARB.write_text(text, encoding="utf-8", newline="\n")

# --------------------------------------------------------------------
# 2. Disable duplicate TVL monitor loop
# --------------------------------------------------------------------
if BOT.exists():
    backup(BOT)
    text = BOT.read_text(encoding="utf-8", errors="replace")

    pattern = re.compile(
        r"await\s+asyncio\.gather\(\s*bot\.run_arbitrage_scan\(\),\s*bot\.monitor_pool_tvls\(\)\s*\)",
        re.DOTALL,
    )
    text2, count = pattern.subn("await bot.run_arbitrage_scan()", text, count=1)

    if count:
        print("[PATCH] Disabled parallel TVL monitor")
        BOT.write_text(text2, encoding="utf-8", newline="\n")
    else:
        print("[INFO] Parallel TVL gather block not found or already disabled")

# --------------------------------------------------------------------
# 3. 20+ DEX registry scaffold
# --------------------------------------------------------------------
write(CORE / "dex_registry.py", r'''
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DexFamily(str, Enum):
    V2_CPMM = "v2_cpmm"
    V3_CLMM = "v3_clmm"
    ALGEBRA_CLMM = "algebra_clmm"
    CURVE_STABLE = "curve_stable"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    AGGREGATOR = "aggregator"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DexDefinition:
    name: str
    family: DexFamily
    factory: str | None
    router: str | None
    fee_bps: int | None
    execution_supported: bool
    notes: str = ""


POLYGON_DEX_REGISTRY: dict[str, DexDefinition] = {
    # V2 CPMM, executable once reserves + calldata are verified
    "quickswap_v2": DexDefinition("quickswap_v2", DexFamily.V2_CPMM, "0x5757371414417b8c6caad45baef941abc7d3ab32", None, 30, True),
    "sushiswap_v2": DexDefinition("sushiswap_v2", DexFamily.V2_CPMM, "0xc35dadb65012ec5796536bd9864ed8773abc74c4", None, 30, True),
    "apeswap_v2": DexDefinition("apeswap_v2", DexFamily.V2_CPMM, "0xcf083be4164828f00cae704ec15a36d711491284", None, 20, True),
    "dfyn_v2": DexDefinition("dfyn_v2", DexFamily.V2_CPMM, "0xe7fb3e833efe5f9c441105eb65ef8b261266423b", None, 30, True),
    "jetswap_v2": DexDefinition("jetswap_v2", DexFamily.V2_CPMM, "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb", None, 30, True),

    # Added candidates. Verify factory/router before enabling execution.
    "comethswap": DexDefinition("comethswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "polycat": DexDefinition("polycat", DexFamily.V2_CPMM, None, None, 30, False, "legacy; verify liquidity"),
    "waultswap": DexDefinition("waultswap", DexFamily.V2_CPMM, None, None, 20, False, "verify factory/router"),
    "firebird": DexDefinition("firebird", DexFamily.V2_CPMM, None, None, 30, False, "verify exact model"),
    "cafeswap": DexDefinition("cafeswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "gravity": DexDefinition("gravity", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "elk": DexDefinition("elk", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "luaswap": DexDefinition("luaswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "polyzap": DexDefinition("polyzap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "swapr": DexDefinition("swapr", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),

    # V3 / Algebra: discovery-visible, execution-blocked until tick math + calldata pass fork sim
    "uniswap_v3": DexDefinition("uniswap_v3", DexFamily.V3_CLMM, "0x1f98431c8ad98523631ae4a59f267346ea31f984", None, None, False),
    "quickswap_v3_algebra": DexDefinition("quickswap_v3_algebra", DexFamily.ALGEBRA_CLMM, None, None, None, False, "requires Algebra adapter"),
    "retro": DexDefinition("retro", DexFamily.ALGEBRA_CLMM, None, None, None, False, "verify factory/router"),
    "pearl": DexDefinition("pearl", DexFamily.ALGEBRA_CLMM, None, None, None, False, "verify factory/router"),
    "kyber_elastic": DexDefinition("kyber_elastic", DexFamily.V3_CLMM, None, None, None, False, "separate model may be required"),

    # Curve / Balancer
    "curve": DexDefinition("curve", DexFamily.CURVE_STABLE, None, None, None, False, "requires Curve registry + invariant adapter"),
    "balancer_weighted": DexDefinition("balancer_weighted", DexFamily.BALANCER_WEIGHTED, "balancer_vault", None, None, False),
    "balancer_stable": DexDefinition("balancer_stable", DexFamily.BALANCER_STABLE, "balancer_vault", None, None, False),

    # Aggregators: quote candidates only, not direct DEX pools
    "zero_x": DexDefinition("zero_x", DexFamily.AGGREGATOR, None, None, None, False),
    "oneinch": DexDefinition("oneinch", DexFamily.AGGREGATOR, None, None, None, False),
    "paraswap": DexDefinition("paraswap", DexFamily.AGGREGATOR, None, None, None, False),
    "odos": DexDefinition("odos", DexFamily.AGGREGATOR, None, None, None, False),
    "openocean": DexDefinition("openocean", DexFamily.AGGREGATOR, None, None, None, False),
    "kyber_aggregator": DexDefinition("kyber_aggregator", DexFamily.AGGREGATOR, None, None, None, False),
}


def executable_v2_factories() -> dict[str, str]:
    return {
        name: dex.factory
        for name, dex in POLYGON_DEX_REGISTRY.items()
        if dex.family == DexFamily.V2_CPMM and dex.execution_supported and dex.factory
    }
''')

# --------------------------------------------------------------------
# 4. Redis hot cache
# --------------------------------------------------------------------
write(CORE / "redis_state.py", r'''
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
''')

# --------------------------------------------------------------------
# 5. Fork simulator health
# --------------------------------------------------------------------
write(CORE / "fork_simulator.py", r'''
from __future__ import annotations

import json
import os
import aiohttp


async def fork_healthcheck(fork_url: str | None = None, timeout_s: float = 2.0) -> bool:
    url = fork_url or os.getenv("FORK_RPC_URL", "http://127.0.0.1:8545")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status < 200 or resp.status >= 300:
                    return False
                body = json.loads(await resp.text())
                return isinstance(body.get("result"), str)
    except Exception:
        return False


def anvil_command() -> str:
    rpc = os.getenv("POLYGON_RPC_URL") or os.getenv("ACTIVE_EXECUTION_RPC") or "<POLYGON_RPC_URL>"
    return f'anvil --fork-url "{rpc}" --chain-id 137 --host 127.0.0.1 --port 8545'
''')

# --------------------------------------------------------------------
# 6. Multicall reserve scaffold
# --------------------------------------------------------------------
write(CORE / "multicall_reserves.py", r'''
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReserveSnapshot:
    pool: str
    reserve0: int
    reserve1: int
    block_number: int | None = None


class MulticallReserveReader:
    """Batch reserve reader scaffold.

    Next implementation:
    - aggregate V2 getReserves()
    - aggregate V3 slot0()/liquidity()
    - return block-tagged snapshots
    """

    async def read_v2_reserves(self, pools: list[str]) -> list[ReserveSnapshot]:
        return []
''')

# --------------------------------------------------------------------
# 7. WSS event indexer scaffold
# --------------------------------------------------------------------
write(CORE / "onchain_event_indexer.py", r'''
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
''')

# --------------------------------------------------------------------
# 8. Pool state cache
# --------------------------------------------------------------------
write(CORE / "pool_state_cache.py", r'''
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CachedPoolState:
    pool: str
    payload: dict[str, Any]
    updated_at: float
    block_number: int | None = None


class PoolStateCache:
    def __init__(self):
        self._cache: dict[str, CachedPoolState] = {}

    def put(self, pool: str, payload: dict[str, Any], block_number: int | None = None) -> None:
        self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)

    def get(self, pool: str) -> CachedPoolState | None:
        return self._cache.get(pool.lower())

    def all(self) -> list[CachedPoolState]:
        return list(self._cache.values())
''')

# --------------------------------------------------------------------
# 9. Nonce/lane lock
# --------------------------------------------------------------------
write(CORE / "nonce_lane_lock.py", r'''
from __future__ import annotations

import asyncio
from collections import defaultdict


class NonceLaneLock:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock_for(self, wallet: str, lane_id: str = "default") -> asyncio.Lock:
        return self._locks[f"{wallet.lower()}:{lane_id}"]
''')

# --------------------------------------------------------------------
# 10. Execution transport selector
# --------------------------------------------------------------------
write(CORE / "execution_transport_selector.py", r'''
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionTransport:
    mode: str
    url: str


def select_execution_transport(prefer_relay: bool = True) -> ExecutionTransport:
    relay = os.getenv("ACTIVE_PRIVATE_RELAY")
    rpc = os.getenv("ACTIVE_EXECUTION_RPC") or os.getenv("POLYGON_RPC_URL")

    if prefer_relay and relay:
        return ExecutionTransport("relay", relay)
    if rpc:
        return ExecutionTransport("rpc", rpc)
    return ExecutionTransport("none", "")
''')

# --------------------------------------------------------------------
# 11. .env safe latency/default config
# --------------------------------------------------------------------
env = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""

defaults = {
    "EXECUTION_ENABLED": "false",
    "DISCOVERY_SOURCE": "onchain_v2",
    "USE_DEXSCREENER": "false",
    "REDIS_ENABLED": "false",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "FORK_RPC_URL": "http://127.0.0.1:8545",
    "REQUIRE_FORK_SIM": "true",
    "ONCHAIN_DISCOVERY_MAX_TOKENS": "80",
    "ONCHAIN_DISCOVERY_MAX_PAIRS": "2500",
    "ONCHAIN_DISCOVERY_CONCURRENCY": "32",
    "ALLOW_V3_EXECUTION": "false",
    "ALLOW_CURVE_EXECUTION": "false",
    "ALLOW_BALANCER_EXECUTION": "false",
    "REQUIRE_POOL_MATH_PROFILE": "true",
}
for k, v in defaults.items():
    env = set_env(env, k, v)

if ENV.exists():
    backup(ENV)
ENV.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env safe defaults updated")

# --------------------------------------------------------------------
# 12. Compile
# --------------------------------------------------------------------
targets = [
    CORE / "dex_registry.py",
    CORE / "redis_state.py",
    CORE / "fork_simulator.py",
    CORE / "multicall_reserves.py",
    CORE / "onchain_event_indexer.py",
    CORE / "pool_state_cache.py",
    CORE / "nonce_lane_lock.py",
    CORE / "execution_transport_selector.py",
    CORE / "polygon_arbitrage.py",
    BOT,
]
for t in targets:
    compile_py(t)

print("")
print("[DONE] Low-latency fix pack installed.")
print("")
print("Next:")
print("  1. Start Redis if you want hot cache: redis-server")
print("  2. Start Anvil fork before execution:")
print("     anvil --fork-url %POLYGON_RPC_URL% --chain-id 137 --host 127.0.0.1 --port 8545")
print("  3. Boot:")
print("     powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
