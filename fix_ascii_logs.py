from pathlib import Path
from datetime import datetime

path = Path("python/polygon_arbitrage_bot.py")
backup = path.with_suffix(path.suffix + f".bak_ascii_{datetime.now():%Y%m%d_%H%M%S}")
backup.write_bytes(path.read_bytes())

text = path.read_text(encoding="utf-8", errors="replace")

replacements = {
    "": "[TARGET]",
    "": "[SCAN]",
    "": "[METRICS]",
    "": "[START]",
    "": "[LOOP]",
    "": "[INTAKE]",
    "": "[OK]",
    "": "[PERF]",
    "": "[TVL]",
    "": "[USD]",
    "": "[TOP]",
    "": "[ROUTE]",
    "": "[EXEC]",
    "": "[TX]",
    "": "[FAIL]",
    "": "[SKIP]",
    "": "[DONE]",
    "": "[WAIT]",
    "": "[ROUTES]",
    "": "-",
    "ðŸŽ": "[TARGET]",
    "ðŸ": "[SCAN]",
    "ðŸŠ": "[METRICS]",
    "ðŸš": "[START]",
    "ðŸ": "[LOOP]",
    "ðŸ": "[INTAKE]",
    "âœ": "[OK]",
    "ðŸ": "[PERF]",
    "ðŸ": "[TVL]",
    "ðŸµ": "[USD]",
    "ðŸ": "[TOP]",
    "âš": "[EXEC]",
    "ðŸ": "[TX]",
    "âŒ": "[FAIL]",
    "â": "-",
}

for bad, good in replacements.items():
    text = text.replace(bad, good)

path.write_text(text, encoding="utf-8", newline="\n")
print(f"ASCII log cleanup complete. Backup: {backup}")
