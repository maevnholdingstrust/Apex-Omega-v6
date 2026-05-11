from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys, re

ROOT = Path.cwd()
path = ROOT / "python" / "apex_omega_core" / "core" / "polygon_arbitrage.py"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

backup = path.with_suffix(path.suffix + f".bak_fix_tvl_nameerror_{stamp}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

# Fix undefined tvl_usd in token metadata merge.
text = text.replace(
    '{"address": normalized, "symbol": "", "tvl_usd": tvl_usd, "discovery_attempts": 0}',
    '{"address": normalized, "symbol": "", "tvl_usd": 0.0, "tvl_verified": False, "discovery_attempts": 0}'
)

# Broader safety: any remaining literal token metadata usage of undefined tvl_usd.
text = re.sub(
    r'("tvl_usd"\s*:\s*)tvl_usd',
    r'\g<1>0.0',
    text
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

print("[OK] fixed undefined tvl_usd in token registry merge")
