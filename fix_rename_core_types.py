from pathlib import Path
from datetime import datetime
import re
import shutil
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD = ROOT / "python" / "apex_omega_core" / "core" / "types.py"
NEW = ROOT / "python" / "apex_omega_core" / "core" / "domain_types.py"

PY_ROOT = ROOT / "python"

def backup_file(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak_rename_types_{STAMP}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] {path} -> {bak}")

def update_text_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8", errors="replace")
    updated = original

    # Only update repo-relative imports.
    # Do NOT touch Python stdlib: from types import MappingProxyType, etc.
    replacements = [
        (r"from\s+\.types\s+import\s+", "from .domain_types import "),
        (r"from\s+apex_omega_core\.core\.types\s+import\s+", "from apex_omega_core.core.domain_types import "),
        (r"import\s+apex_omega_core\.core\.types\s+as\s+", "import apex_omega_core.core.domain_types as "),
    ]

    for pattern, repl in replacements:
        updated = re.sub(pattern, repl, updated)

    # Handle rare exact package import form:
    updated = updated.replace("apex_omega_core.core.types", "apex_omega_core.core.domain_types")

    if updated != original:
        backup_file(path)
        path.write_text(updated, encoding="utf-8", newline="\n")
        print(f"[UPDATED] {path}")
        return True

    return False

def main():
    if not OLD.exists():
        print(f"[INFO] No old types.py found at: {OLD}")
        if NEW.exists():
            print(f"[OK] domain_types.py already exists: {NEW}")
        return 0

    backup_file(OLD)

    if NEW.exists():
        print(f"[WARN] {NEW} already exists. Keeping existing domain_types.py.")
        print(f"[WARN] Old file remains: {OLD}")
    else:
        OLD.rename(NEW)
        print(f"[RENAMED] {OLD} -> {NEW}")

    updated_count = 0
    for path in PY_ROOT.rglob("*.py"):
        # Skip backup files if any have .py suffix somehow.
        if ".bak_" in path.name:
            continue
        if path == NEW:
            continue
        if update_text_file(path):
            updated_count += 1

    print(f"[SUMMARY] Updated {updated_count} Python files.")

    print("")
    print("[VERIFY] Searching for remaining repo .types imports...")
    remaining = []
    for path in PY_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "from .types import" in text or "apex_omega_core.core.types" in text:
            remaining.append(path)

    if remaining:
        print("[FAIL] Remaining references:")
        for p in remaining:
            print(f"  {p}")
        return 2

    print("[OK] No remaining repo .types imports found.")

    # Verify monitor compile if present.
    monitor_candidates = [
        ROOT / "tools" / "endpoint_latency_monitor.py",
        ROOT / "python" / "apex_omega_core" / "core" / "endpoint_latency_monitor.py",
    ]

    for monitor in monitor_candidates:
        if monitor.exists():
            print(f"[VERIFY] py_compile {monitor}")
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(monitor)],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                print("[FAIL] py_compile failed")
                print(result.stdout)
                print(result.stderr)
                return result.returncode
            print("[OK] monitor compiles")

    print("")
    print("[DONE] types.py shadow fix complete.")
    print("")
    print("Next commands:")
    print("  python .\\tools\\endpoint_latency_monitor.py")
    print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
