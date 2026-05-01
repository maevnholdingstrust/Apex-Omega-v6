from pathlib import Path
from datetime import datetime
import subprocess
import sys

ROOT = Path.cwd()
path = ROOT / "python" / "polygon_arbitrage_bot.py"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = path.with_suffix(path.suffix + f".bak_deep_header_clean_{stamp}")
backup.write_bytes(path.read_bytes())
print(f"[BACKUP] {backup}")

text = path.read_bytes().decode("utf-8-sig", errors="replace")
text = text.replace("\ufeff", "")

lines = text.splitlines()

# Find first real Python line. Everything before it is treated as damaged header debris.
real_starts = (
    "import ",
    "from ",
    "class ",
    "def ",
    "async def ",
    "@",
    '"""',
    "'''",
)

start_idx = None
for i, line in enumerate(lines):
    s = line.strip()

    if not s:
        continue

    # Remove known corrupted patch debris.
    compact = s.replace("-", "").replace(" ", "").replace("#", "").lower()
    if (
        "apexpatch" in compact
        or "windowsutf8" in compact
        or "stdoutstderr" in compact
        or "endapexpatch" in compact
        or s in {"-", "--", "---", "-#", "#-"}
        or all(ch in "-# " for ch in s)
    ):
        continue

    if s.startswith(real_starts):
        start_idx = i
        break

# If no clean start found, fail safely.
if start_idx is None:
    print("[FAIL] Could not find first real Python statement.")
    print("First 40 lines:")
    for n, line in enumerate(lines[:40], start=1):
        print(f"{n:03}: {line!r}")
    raise SystemExit(2)

body = "\n".join(lines[start_idx:]).lstrip()

# Remove any clean duplicate APEX patch block from body.
marker_start = "# --- APEX PATCH: Windows UTF-8 stdout/stderr safety ---"
marker_end = "# --- END APEX PATCH ---"

while marker_start in body and marker_end in body:
    start = body.find(marker_start)
    end = body.find(marker_end, start)
    if end == -1:
        break
    end += len(marker_end)
    body = (body[:start] + body[end:]).lstrip()

# Remove remaining top-level single-character junk lines anywhere near top.
body_lines = body.splitlines()
clean_body_lines = []
for idx, line in enumerate(body_lines):
    s = line.strip()
    if idx < 50 and s in {"-", "--", "---", "-#", "#-"}:
        continue
    if idx < 50 and all(ch in "-# " for ch in s) and s:
        continue
    clean_body_lines.append(line)

body = "\n".join(clean_body_lines).lstrip()

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

path.write_text(header + body, encoding="utf-8", newline="\n")
print("[WRITE] Deep-cleaned polygon_arbitrage_bot.py header")

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
    print("First 35 lines after repair:")
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()[:35], start=1):
        print(f"{n:03}: {line!r}")
    raise SystemExit(result.returncode)

print("[OK] polygon_arbitrage_bot.py compiles")
