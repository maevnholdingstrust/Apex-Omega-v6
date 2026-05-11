from pathlib import Path
from datetime import datetime
import shutil
import re
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

BOT = ROOT / "python" / "polygon_arbitrage_bot.py"
ARB = ROOT / "python" / "apex_omega_core" / "core" / "polygon_arbitrage.py"
CORE = ROOT / "python" / "apex_omega_core" / "core"
ENV = ROOT / ".env"

def backup(path: Path, label: str):
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak_{label}_{STAMP}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] {path} -> {bak}")

def compile_py(path: Path):
    if not path.exists():
        print(f"[SKIP] Missing {path}")
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

# ============================================================
# 1. Ensure fork simulation helper exists
# ============================================================

fork_sim = CORE / "fork_simulator.py"
if not fork_sim.exists():
    fork_sim.write_text(r'''
from __future__ import annotations

import json
import os
import aiohttp


async def fork_healthcheck(fork_url: str | None = None, timeout_s: float = 2.0) -> bool:
    url = fork_url or os.getenv("ACTIVE_FORK_RPC_URL") or os.getenv("FORK_RPC_URL", "http://127.0.0.1:8545")
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
''', encoding="utf-8", newline="\n")
    print(f"[WRITE] {fork_sim}")

# ============================================================
# 2. Patch Phase 2 to reuse Phase 1 pools where possible
# ============================================================

if not BOT.exists():
    raise FileNotFoundError(BOT)

backup(BOT, "full_stack_fix_bot")
bot_text = BOT.read_text(encoding="utf-8", errors="replace")

# Replace known Phase 2 rescan patterns.
phase2_patterns = [
    (
        r"opportunities\s*=\s*await\s+self\.arbitrage_detector\.find_arbitrage_opportunities\(\s*self\.tokens\s*\)",
        "opportunities = await self.arbitrage_detector.find_arbitrage_opportunities_from_pools(pools)"
    ),
    (
        r"opportunities\s*=\s*await\s*self\.detector\.find_arbitrage_opportunities\(\s*self\.tokens\s*\)",
        "opportunities = await self.detector.find_arbitrage_opportunities_from_pools(pools)"
    ),
    (
        r"opportunities\s*=\s*await\s*arbitrage_detector\.find_arbitrage_opportunities\(\s*tokens\s*\)",
        "opportunities = await arbitrage_detector.find_arbitrage_opportunities_from_pools(pools)"
    ),
]

phase2_replacements = 0
for pattern, repl in phase2_patterns:
    bot_text, count = re.subn(pattern, repl, bot_text)
    phase2_replacements += count

if phase2_replacements:
    print(f"[PATCH] Phase 2 now reuses Phase 1 pools: {phase2_replacements} replacement(s)")
else:
    print("[INFO] No exact Phase 2 rescan pattern found. Printing opportunity calls:")
    for n, line in enumerate(bot_text.splitlines(), start=1):
        if "find_arbitrage_opportunities" in line:
            print(f"  {n}: {line}")

# Disable parallel TVL monitor if gather still exists.
gather_pattern = re.compile(
    r"await\s+asyncio\.gather\(\s*bot\.run_arbitrage_scan\(\),\s*bot\.monitor_pool_tvls\(\)\s*\)",
    re.DOTALL,
)
bot_text, gather_count = gather_pattern.subn("await bot.run_arbitrage_scan()", bot_text, count=1)
if gather_count:
    print("[PATCH] Disabled duplicate parallel TVL monitor")

# Patch misleading TVL display.
bot_text = bot_text.replace(
    'logger.info(f"    Total TVL scanned: ${total_tvl:,.0f} USD")',
    'logger.info(f"    Total TVL scanned: UNVALUED - {len(pools)} pools have raw reserves, USD valuation pending")'
)
bot_text = bot_text.replace(
    'logger.info(f" TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")',
    'logger.info(f" TOTAL TVL: UNVALUED across {len(pools)} pools - raw reserves discovered, USD valuation pending")'
)
bot_text = bot_text.replace(
    'logger.info(f"   [TVL] Total TVL scanned: ${total_tvl:,.0f} USD")',
    'logger.info(f"   [TVL] Total TVL scanned: UNVALUED - {len(pools)} pools have raw reserves, USD valuation pending")'
)
bot_text = bot_text.replace(
    'logger.info(f"[USD] TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")',
    'logger.info(f"[USD] TOTAL TVL: UNVALUED across {len(pools)} pools - raw reserves discovered, USD valuation pending")'
)

BOT.write_text(bot_text, encoding="utf-8", newline="\n")

# ============================================================
# 3. Patch detector with pool-based method and constructor-safe pool converter
# ============================================================

if not ARB.exists():
    raise FileNotFoundError(ARB)

backup(ARB, "full_stack_fix_arb")
arb_text = ARB.read_text(encoding="utf-8", errors="replace")

if "import inspect" not in arb_text:
    if "import os" in arb_text:
        arb_text = arb_text.replace("import os", "import os\nimport inspect", 1)
    else:
        arb_text = "import inspect\n" + arb_text
    print("[PATCH] Added inspect import")

if "from .pool_math_registry import classify_pool_kwargs" not in arb_text:
    marker = "from .onchain_v2_discovery import"
    if marker in arb_text:
        arb_text = arb_text.replace(marker, "from .pool_math_registry import classify_pool_kwargs\n" + marker, 1)
    else:
        arb_text = "from .pool_math_registry import classify_pool_kwargs\n" + arb_text
    print("[PATCH] Added classify_pool_kwargs import")

if "find_arbitrage_opportunities_from_pools" not in arb_text:
    method = r'''
    async def find_arbitrage_opportunities_from_pools(self, pools: List[Pool]) -> List[ArbitrageOpportunity]:
        """Find opportunities from already-discovered pools.

        Phase 1 performs on-chain discovery. Phase 2 must reuse those pools
        and must not rescan factories.
        """
        if not pools:
            return []

        for method_name in (
            "_find_opportunities_from_pools",
            "_detect_opportunities_from_pools",
            "_analyze_pools_for_arbitrage",
            "_find_arbitrage_from_pools",
        ):
            fn = getattr(self, method_name, None)
            if fn:
                result = fn(pools)
                if hasattr(result, "__await__"):
                    result = await result
                return result or []

        logger.warning(
            "Pool-based opportunity detector not wired yet; returning 0 opportunities without rescanning."
        )
        return []

'''
    marker = "    async def find_arbitrage_opportunities"
    if marker in arb_text:
        arb_text = arb_text.replace(marker, method + "\n" + marker, 1)
        print("[PATCH] Added find_arbitrage_opportunities_from_pools")
    else:
        print("[WARN] Could not locate find_arbitrage_opportunities insertion point")

# Force-replace _pool_from_onchain_v2 if it still passes fee_bps directly.
lines = arb_text.splitlines()
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
    old_block = "\n".join(lines[start:end])
    if "fee_bps=30" in old_block or "pool_type=\"v2\"" in old_block or "Constructor-safe" not in old_block:
        lines = lines[:start] + new_func.splitlines() + [""] + lines[end:]
        arb_text = "\n".join(lines) + "\n"
        print("[PATCH] Force-replaced _pool_from_onchain_v2")
    else:
        print("[INFO] _pool_from_onchain_v2 already constructor-safe")
else:
    print("[WARN] _pool_from_onchain_v2 not found")

ARB.write_text(arb_text, encoding="utf-8", newline="\n")

# ============================================================
# 4. Safe .env defaults
# ============================================================

env = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
for k, v in {
    "EXECUTION_ENABLED": "false",
    "REQUIRE_FORK_SIM": "true",
    "USE_DEXSCREENER": "false",
    "DISCOVERY_SOURCE": "onchain_v2",
    "FORK_RPC_URL": "http://127.0.0.1:8545",
    "ONCHAIN_DISCOVERY_MAX_TOKENS": "80",
    "ONCHAIN_DISCOVERY_MAX_PAIRS": "2500",
    "ONCHAIN_DISCOVERY_CONCURRENCY": "32",
    "ALLOW_V3_EXECUTION": "false",
    "ALLOW_CURVE_EXECUTION": "false",
    "ALLOW_BALANCER_EXECUTION": "false",
}.items():
    env = set_env(env, k, v)

backup(ENV, "full_stack_fix_env")
ENV.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env safe defaults enforced")

# ============================================================
# 5. Compile
# ============================================================

for target in [fork_sim, ARB, BOT]:
    compile_py(target)

print("")
print("[DONE] FULL_STACK_FIX applied.")
print("")
print("Expected after reboot:")
print("  - FORK_RPC remains OK")
print("  - Phase 2 does not perform another full on-chain scan")
print("  - No Pool(... fee_bps=...) constructor crash")
print("  - TVL shows UNVALUED until valuation is wired")
print("  - EXECUTION_ENABLED remains false")
print("")
print("Run:")
print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
