from pathlib import Path
from datetime import datetime
import subprocess
import sys

ROOT = Path.cwd()
path = ROOT / "python" / "polygon_arbitrage_bot.py"

if not path.exists():
    raise FileNotFoundError(path)

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = path.with_suffix(path.suffix + f".bak_fix_bom_header_{stamp}")
backup.write_bytes(path.read_bytes())
print(f"[BACKUP] {backup}")

raw = path.read_bytes()

# Decode aggressively and remove BOM / non-printable BOM wherever it appears.
text = raw.decode("utf-8-sig", errors="replace")
text = text.replace("\ufeff", "")

lines = text.splitlines()

# Remove corrupted APEX PATCH header lines at top.
# The bad version looks like:
# --#- ------- -A-P-E-X- -P-A-T-C-H-...
cleaned = []
skip_mode = True

for i, line in enumerate(lines):
    stripped = line.strip()

    if skip_mode:
        compact = stripped.replace("-", "").replace(" ", "").lower()

        # Skip corrupted patch/comment fragments at very top only.
        if (
            i < 40
            and (
                "apexpatch" in compact
                or "windowsutf8" in compact
                or "stdoutstderr" in compact
                or stripped.startswith("-#-")
                or stripped.startswith("--#")
                or stripped.startswith("-")
                or stripped == ""
            )
        ):
            continue

        # Stop skipping once real Python starts.
        skip_mode = False

    cleaned.append(line)

body = "\n".join(cleaned).lstrip()

header = '''# --- APEX PATCH: Windows UTF-8 stdout/stderr safety ---
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# --- END APEX PATCH ---

'''

# Avoid duplicate clean header.
if "APEX PATCH: Windows UTF-8 stdout/stderr safety" in body:
    start = body.find("# --- APEX PATCH: Windows UTF-8 stdout/stderr safety ---")
    end_marker = "# --- END APEX PATCH ---"
    end = body.find(end_marker, start)
    if start != -1 and end != -1:
        end += len(end_marker)
        body = body[:start] + body[end:]
        body = body.lstrip()

path.write_text(header + body, encoding="utf-8", newline="\n")
print("[WRITE] Cleaned polygon_arbitrage_bot.py header")

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

print("[OK] polygon_arbitrage_bot.py compiles")
