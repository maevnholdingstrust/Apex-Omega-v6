from pathlib import Path
from datetime import datetime
import shutil
import re

path = Path("python/polygon_arbitrage_bot.py")
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = path.with_suffix(path.suffix + f".bak_disable_parallel_tvl_{stamp}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

pattern = re.compile(
    r"await\s+asyncio\.gather\(\s*bot\.run_arbitrage_scan\(\),\s*bot\.monitor_pool_tvls\(\)\s*\)",
    re.DOTALL,
)

new_text, count = pattern.subn("await bot.run_arbitrage_scan()", text, count=1)

if count:
    path.write_text(new_text, encoding="utf-8", newline="\n")
    print("[PATCH] Disabled parallel TVL monitor")
else:
    print("[INFO] gather block not found or already disabled")
