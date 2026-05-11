from pathlib import Path
from datetime import datetime
import shutil
import re
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
PY = ROOT / "python"

patterns = [
    (r"from\s+\.types\s+import\s+", "from .domain_types import "),
    (r"from\s+apex_omega_core\.core\.types\s+import\s+", "from apex_omega_core.core.domain_types import "),
    (r"import\s+apex_omega_core\.core\.types\s+as\s+", "import apex_omega_core.core.domain_types as "),
]

updated_files = []

for path in PY.rglob("*.py"):
    if ".bak_" in path.name:
        continue

    text = path.read_text(encoding="utf-8", errors="replace")
    updated = text

    # Only rewrite .types inside apex_omega_core/core files.
    # Do NOT touch ssot_pipeline/from .types imports.
    if "apex_omega_core\\core" in str(path) or "apex_omega_core/core" in str(path):
        for pattern, repl in patterns:
            updated = re.sub(pattern, repl, updated)
        updated = updated.replace("apex_omega_core.core.types", "apex_omega_core.core.domain_types")

    if updated != text:
        backup = path.with_suffix(path.suffix + f".bak_core_import_fix_{STAMP}")
        shutil.copy2(path, backup)
        path.write_text(updated, encoding="utf-8", newline="\n")
        updated_files.append(path)
        print(f"[UPDATED] {path}")
        print(f"[BACKUP] {backup}")

print(f"[SUMMARY] Updated {len(updated_files)} files")

# Verify no bad core imports remain.
bad = []
for path in (PY / "apex_omega_core" / "core").rglob("*.py"):
    text = path.read_text(encoding="utf-8", errors="replace")
    if "from .types import" in text or "apex_omega_core.core.types" in text:
        bad.append(path)

if bad:
    print("[FAIL] Remaining bad core imports:")
    for p in bad:
        print("  " + str(p))
    raise SystemExit(2)

print("[OK] No remaining apex_omega_core.core.types imports")

# Compile the specific failing chain.
targets = [
    PY / "apex_omega_core" / "core" / "inference.py",
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

print("[DONE] Core import fix complete")
