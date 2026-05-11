from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys

ROOT = Path.cwd()
PY = ROOT / "python"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

replacements = {
    "from apex_omega_core.core.types import": "from apex_omega_core.core.domain_types import",
    "import apex_omega_core.core.types as": "import apex_omega_core.core.domain_types as",
    "apex_omega_core.core.types": "apex_omega_core.core.domain_types",
}

updated = []

for path in PY.rglob("*.py"):
    if ".bak_" in path.name:
        continue

    text = path.read_text(encoding="utf-8", errors="replace")
    new = text

    for old, repl in replacements.items():
        new = new.replace(old, repl)

    if new != text:
        backup = path.with_suffix(path.suffix + f".bak_abs_core_types_fix_{STAMP}")
        shutil.copy2(path, backup)
        path.write_text(new, encoding="utf-8", newline="\n")
        updated.append(path)
        print(f"[UPDATED] {path}")
        print(f"[BACKUP] {backup}")

print(f"[SUMMARY] Updated {len(updated)} files")

bad = []
for path in PY.rglob("*.py"):
    if ".bak_" in path.name:
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    if "apex_omega_core.core.types" in text:
        bad.append(path)

if bad:
    print("[FAIL] Remaining absolute old imports:")
    for p in bad:
        print("  " + str(p))
    raise SystemExit(2)

print("[OK] No remaining apex_omega_core.core.types absolute imports")

targets = [
    PY / "apex_omega_core" / "strategies" / "c1_aggressor_apex.py",
    PY / "apex_omega_core" / "strategies" / "execution_router.py",
    PY / "polygon_arbitrage_bot.py",
]

for target in targets:
    if target.exists():
        print(f"[VERIFY] py_compile {target}")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(target)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            raise SystemExit(result.returncode)

print("[DONE] Absolute core types import fix complete")
