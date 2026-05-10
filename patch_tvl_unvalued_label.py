from pathlib import Path
from datetime import datetime
import shutil
import re
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
bot = ROOT / "python" / "polygon_arbitrage_bot.py"

backup = bot.with_suffix(bot.suffix + f".bak_tvl_unvalued_label_{STAMP}")
shutil.copy2(bot, backup)
print(f"[BACKUP] {backup}")

text = bot.read_text(encoding="utf-8", errors="replace")

# Replace misleading total TVL log phrases with explicit "unvalued" wording.
text = text.replace(
    'logger.info(f"    Total TVL scanned: ${total_tvl:,.0f} USD")',
    'logger.info(f"    Total TVL scanned: UNVALUED - {len(pools)} pools have raw reserves, USD valuation pending")'
)

text = text.replace(
    'logger.info(f" TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")',
    'logger.info(f" TOTAL TVL: UNVALUED across {len(pools)} pools - raw reserves discovered, USD valuation pending")'
)

# Also catch ASCII variants if prior cleanup changed symbols.
text = text.replace(
    'logger.info(f"   [TVL] Total TVL scanned: ${total_tvl:,.0f} USD")',
    'logger.info(f"   [TVL] Total TVL scanned: UNVALUED - {len(pools)} pools have raw reserves, USD valuation pending")'
)

text = text.replace(
    'logger.info(f"[USD] TOTAL TVL: ${total_tvl:,.0f} USD across {len(pools)} pools")',
    'logger.info(f"[USD] TOTAL TVL: UNVALUED across {len(pools)} pools - raw reserves discovered, USD valuation pending")'
)

bot.write_text(text, encoding="utf-8", newline="\n")

result = subprocess.run(
    [sys.executable, "-m", "py_compile", str(bot)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)

print("[OK] TVL log label patched to UNVALUED")
