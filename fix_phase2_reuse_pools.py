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

def backup(path: Path, label: str):
    bak = path.with_suffix(path.suffix + f".bak_{label}_{STAMP}")
    shutil.copy2(path, bak)
    print(f"[BACKUP] {path} -> {bak}")

def compile_py(path: Path):
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

if not BOT.exists():
    raise FileNotFoundError(BOT)
if not ARB.exists():
    raise FileNotFoundError(ARB)

# --------------------------------------------------------------------
# 1. Patch detector: add pool-reuse method if missing
# --------------------------------------------------------------------
backup(ARB, "phase2_pool_reuse_detector")
arb_text = ARB.read_text(encoding="utf-8", errors="replace")

if "find_arbitrage_opportunities_from_pools" not in arb_text:
    method = r'''
    async def find_arbitrage_opportunities_from_pools(self, pools: List[Pool]) -> List[ArbitrageOpportunity]:
        """Find opportunities from already-discovered pools.

        Phase 1 already performed on-chain discovery. Phase 2 must not
        rescan factories; it must reuse the pool list from Phase 1.
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
            "Pool-based opportunity detector not wired yet; "
            "returning 0 opportunities without rescanning."
        )
        return []

'''
    marker = "    async def find_arbitrage_opportunities"
    if marker in arb_text:
        arb_text = arb_text.replace(marker, method + "\n" + marker, 1)
        print("[PATCH] Added find_arbitrage_opportunities_from_pools")
    else:
        print("[WARN] Could not find find_arbitrage_opportunities insertion point")
else:
    print("[INFO] find_arbitrage_opportunities_from_pools already exists")

ARB.write_text(arb_text, encoding="utf-8", newline="\n")

# --------------------------------------------------------------------
# 2. Patch bot: replace Phase 2 token-based rescan with Phase 1 pool reuse
# --------------------------------------------------------------------
backup(BOT, "phase2_reuse_pools")
bot_text = BOT.read_text(encoding="utf-8", errors="replace")

replacements = [
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

changed = 0
for pattern, repl in replacements:
    bot_text, count = re.subn(pattern, repl, bot_text)
    changed += count

if changed:
    print(f"[PATCH] Replaced {changed} Phase 2 rescan call(s) with pool reuse")
else:
    print("[WARN] No exact Phase 2 rescan call pattern found.")
    print("[INFO] Showing lines containing find_arbitrage_opportunities:")
    for n, line in enumerate(bot_text.splitlines(), start=1):
        if "find_arbitrage_opportunities" in line:
            print(f"{n}: {line}")

BOT.write_text(bot_text, encoding="utf-8", newline="\n")

# --------------------------------------------------------------------
# 3. Compile
# --------------------------------------------------------------------
compile_py(ARB)
compile_py(BOT)

print("[DONE] Phase 2 pool-reuse patch complete.")
print("Run:")
print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
