from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys, re

ROOT = Path.cwd()
PY = ROOT / "python"
CORE = PY / "apex_omega_core" / "core"
BOT = PY / "polygon_arbitrage_bot.py"
ARB = CORE / "polygon_arbitrage.py"
ENV = ROOT / ".env"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p):
    if p.exists():
        b = p.with_suffix(p.suffix + f".bak_latency_fix_{STAMP}")
        shutil.copy2(p, b)
        print(f"[BACKUP] {b}")

def write(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    backup(p)
    p.write_text(s, encoding="utf-8", newline="\n")
    print(f"[WRITE] {p}")

def compile_py(p):
    if not p.exists(): return
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
    print(f"[OK] compiled {p}")

def set_env(src, k, v):
    line = f"{k}={v}"
    rx = re.compile(rf"^{re.escape(k)}=.*$", re.MULTILINE)
    return rx.sub(line, src) if rx.search(src) else src.rstrip() + "\n" + line + "\n"

# 1) Force-fix Pool converter: no constructor fee_bps crash
backup(ARB)
text = ARB.read_text(encoding="utf-8", errors="replace")

if "import inspect" not in text:
    text = text.replace("import os", "import os\nimport inspect", 1) if "import os" in text else "import inspect\n" + text

if "from .pool_math_registry import classify_pool_kwargs" not in text:
    text = "from .pool_math_registry import classify_pool_kwargs\n" + text

lines = text.splitlines()
start = next((i for i,l in enumerate(lines) if l.startswith("    def _pool_from_onchain_v2(")), None)
if start is not None:
    end = len(lines)
    for j in range(start+1, len(lines)):
        if lines[j].startswith("    def ") or lines[j].startswith("    async def "):
            end = j
            break

    fn = r'''    def _pool_from_onchain_v2(self, raw: OnchainV2Pool) -> Pool:
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
            "fee": (classified.fee_bps or 30) / 10000,
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

        allowed = set(inspect.signature(Pool).parameters.keys())
        kwargs = {k: v for k, v in meta.items() if k in allowed}

        try:
            pool = Pool(**kwargs)
        except TypeError:
            fallback_sets = (
                ("address", "dex", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("address", "dex", "token0", "token1", "reserve0", "reserve1"),
                ("pool_address", "dex_name", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("pair_address", "dex_name", "token0", "token1", "reserve0", "reserve1"),
            )
            pool = None
            last = None
            for keys in fallback_sets:
                try:
                    pool = Pool(**{k: meta[k] for k in keys if k in allowed})
                    break
                except TypeError as exc:
                    last = exc
            if pool is None:
                raise last or TypeError("Unable to construct Pool")

        for k, v in meta.items():
            try: setattr(pool, k, v)
            except Exception: pass

        return pool
'''
    text = "\n".join(lines[:start] + fn.splitlines() + [""] + lines[end:]) + "\n"
    print("[PATCH] Pool converter fixed")

ARB.write_text(text, encoding="utf-8", newline="\n")

# 2) Disable duplicate TVL monitor gather
if BOT.exists():
    backup(BOT)
    btxt = BOT.read_text(encoding="utf-8", errors="replace")
    btxt2 = re.sub(
        r"await\s+asyncio\.gather\(\s*bot\.run_arbitrage_scan\(\),\s*bot\.monitor_pool_tvls\(\)\s*\)",
        "await bot.run_arbitrage_scan()",
        btxt,
        count=1,
        flags=re.DOTALL
    )
    BOT.write_text(btxt2, encoding="utf-8", newline="\n")
    print("[PATCH] Parallel TVL monitor disabled if present")

# 3) Low latency modules
write(CORE / "redis_state.py", '''from __future__ import annotations
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
''')

write(CORE / "fork_simulator.py", '''from __future__ import annotations
import os, json, aiohttp
async def fork_healthcheck(fork_url=None, timeout_s=2.0):
    url = fork_url or os.getenv("FORK_RPC_URL", "http://127.0.0.1:8545")
    payload = {"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status < 200 or resp.status >= 300: return False
                return isinstance(json.loads(await resp.text()).get("result"), str)
    except Exception:
        return False
def anvil_command():
    rpc = os.getenv("POLYGON_RPC_URL") or os.getenv("ACTIVE_EXECUTION_RPC") or "<POLYGON_RPC_URL>"
    return f'anvil --fork-url "{rpc}" --chain-id 137 --host 127.0.0.1 --port 8545'
''')

write(CORE / "pool_state_cache.py", '''from __future__ import annotations
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
    def __init__(self): self._cache = {}
    def put(self, pool, payload, block_number=None): self._cache[pool.lower()] = CachedPoolState(pool, payload, time.time(), block_number)
    def get(self, pool): return self._cache.get(pool.lower())
    def all(self): return list(self._cache.values())
''')

write(CORE / "nonce_lane_lock.py", '''from __future__ import annotations
import asyncio
from collections import defaultdict
class NonceLaneLock:
    def __init__(self): self._locks = defaultdict(asyncio.Lock)
    def lock_for(self, wallet, lane_id="default"): return self._locks[f"{wallet.lower()}:{lane_id}"]
''')

write(CORE / "execution_transport_selector.py", '''from __future__ import annotations
import os
from dataclasses import dataclass
@dataclass(frozen=True)
class ExecutionTransport:
    mode: str
    url: str
def select_execution_transport(prefer_relay=True):
    relay = os.getenv("ACTIVE_PRIVATE_RELAY")
    rpc = os.getenv("ACTIVE_EXECUTION_RPC") or os.getenv("POLYGON_RPC_URL")
    if prefer_relay and relay: return ExecutionTransport("relay", relay)
    if rpc: return ExecutionTransport("rpc", rpc)
    return ExecutionTransport("none", "")
''')

write(CORE / "multicall_reserves.py", '''from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ReserveSnapshot:
    pool: str
    reserve0: int
    reserve1: int
    block_number: int | None = None
class MulticallReserveReader:
    async def read_v2_reserves(self, pools: list[str]) -> list[ReserveSnapshot]:
        return []
''')

write(CORE / "onchain_event_indexer.py", '''from __future__ import annotations
import os
class OnchainEventIndexer:
    def __init__(self, wss_url=None):
        self.wss_url = wss_url or os.getenv("ACTIVE_DISCOVERY_WSS") or os.getenv("POLYGON_WSS_ACTIVE")
    async def run(self): raise NotImplementedError("WSS event loop not implemented yet")
''')

# 4) env defaults
env = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
for k, v in {
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
}.items():
    env = set_env(env, k, v)
backup(ENV)
ENV.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env updated")

for p in [
    ARB, BOT,
    CORE / "redis_state.py",
    CORE / "fork_simulator.py",
    CORE / "pool_state_cache.py",
    CORE / "nonce_lane_lock.py",
    CORE / "execution_transport_selector.py",
    CORE / "multicall_reserves.py",
    CORE / "onchain_event_indexer.py",
]:
    compile_py(p)

print("[DONE] Installed low-latency fix pack.")
print("Keep Anvil open in its own terminal, then run:")
print("powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
