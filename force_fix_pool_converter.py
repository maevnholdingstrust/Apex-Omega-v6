from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
path = ROOT / "python" / "apex_omega_core" / "core" / "polygon_arbitrage.py"

if not path.exists():
    raise FileNotFoundError(path)

backup = path.with_suffix(path.suffix + f".bak_force_pool_converter_{STAMP}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

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
    print("[PATCH] Added pool math registry import")

lines = text.splitlines()
start = None

for i, line in enumerate(lines):
    if line.startswith("    def _pool_from_onchain_v2("):
        start = i
        break

if start is None:
    raise RuntimeError("Could not find _pool_from_onchain_v2")

end = len(lines)
for j in range(start + 1, len(lines)):
    line = lines[j]
    if line.startswith("    def ") or line.startswith("    async def "):
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
            # Fallback for older minimal Pool signatures.
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

new_lines = lines[:start] + new_func.splitlines() + [""] + lines[end:]
new_text = "\n".join(new_lines) + "\n"

path.write_text(new_text, encoding="utf-8", newline="\n")
print("[PATCH] Force-replaced _pool_from_onchain_v2")

result = subprocess.run(
    [sys.executable, "-m", "py_compile", str(path)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print("[FAIL] py_compile failed")
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)

print("[OK] polygon_arbitrage.py compiles")

# Show the patched function header area for confirmation.
patched = path.read_text(encoding="utf-8", errors="replace").splitlines()
for idx, line in enumerate(patched):
    if line.startswith("    def _pool_from_onchain_v2("):
        print("[CONFIRM] patched function begins:")
        for out in patched[idx:idx+20]:
            print(out)
        break
