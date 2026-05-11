from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys

ROOT = Path.cwd()
path = ROOT / "python" / "apex_omega_core" / "core" / "all_pool_discovery.py"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

backup = path.with_suffix(path.suffix + f".bak_progress_{stamp}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

# Add bounded gather helper if missing.
if "async def _gather_with_progress" not in text:
    insert = r'''

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
'''
    marker = "async def discover_v2("
    text = text.replace(marker, insert + "\n\n" + marker, 1)
    print("[PATCH] Added progress helper")

# Replace await asyncio.gather calls in discover_v2/discover_v3 task blocks.
text = text.replace(
'''    await asyncio.gather(*[
        one(dex, factory, a, b)
        for dex, factory in V2_FACTORIES.items()
        for a, b in pairs
    ])
''',
'''    tasks = [
        one(dex, factory, a, b)
        for dex, factory in V2_FACTORIES.items()
        for a, b in pairs
    ]
    await _gather_with_progress(tasks, "V2_DISCOVERY", progress_every=500)
'''
)

text = text.replace(
'''    await asyncio.gather(*[
        one(dex, factory, a, b, fee)
        for dex, factory in factories.items()
        for a, b in pairs
        for fee in V3_FEE_TIERS
    ])
''',
'''    tasks = [
        one(dex, factory, a, b, fee)
        for dex, factory in factories.items()
        for a, b in pairs
        for fee in V3_FEE_TIERS
    ]
    await _gather_with_progress(tasks, "V3_DISCOVERY", progress_every=500)
'''
)

path.write_text(text, encoding="utf-8", newline="\n")

result = subprocess.run(
    [sys.executable, "-m", "py_compile", str(path)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)

print("[OK] all_pool_discovery.py patched with progress logging")
