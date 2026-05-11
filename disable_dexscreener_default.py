from pathlib import Path
from datetime import datetime
import shutil
import re
import subprocess
import sys

ROOT = Path.cwd()
path = ROOT / "python" / "apex_omega_core" / "core" / "polygon_arbitrage.py"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

if not path.exists():
    raise FileNotFoundError(path)

backup = path.with_suffix(path.suffix + f".bak_disable_dexscreener_{stamp}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

# Ensure os import exists.
if "import os" not in text:
    text = re.sub(r"(^import\s+asyncio\s*$)", r"\1\nimport os", text, count=1, flags=re.MULTILINE)
    if "import os" not in text:
        text = "import os\n" + text

# Add config fields near token pool cache if present.
old = '''        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}
'''
new = '''        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.use_dexscreener: bool = os.getenv("USE_DEXSCREENER", "false").lower() == "true"
        self.dexscreener_max_tokens_per_scan: int = int(os.getenv("DEXSCREENER_MAX_TOKENS_PER_SCAN", "25"))
'''
if old in text and "self.use_dexscreener" not in text:
    text = text.replace(old, new)
    print("[PATCH] Added USE_DEXSCREENER config")

# Replace _fetch_live_pairs_for_token with a gated version.
pattern = re.compile(
    r'''    async def _fetch_live_pairs_for_token\(self, address: str\) -> List\[Dict\[str, Any\]\]:
        .*?
        return normalized_pairs
''',
    re.DOTALL,
)

replacement = '''    async def _fetch_live_pairs_for_token(self, address: str) -> List[Dict[str, Any]]:
        """Fetch and cache external pair metadata for a token.

        DEXScreener is disabled by default.
        Reason:
        - external API 429s under 500-token scans
        - not authoritative for execution pricing
        - final execution must use on-chain reserves / pool state

        Enable only with USE_DEXSCREENER=true for dashboard enrichment or fallback diagnostics.
        """
        if address in self._token_pool_cache:
            cached = self._token_pool_cache.get(address)
            return cached if isinstance(cached, list) else []

        if not getattr(self, "use_dexscreener", False):
            self._token_pool_cache[address] = []
            return []

        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        except Exception as exc:
            logger.exception("DEXScreener pair fetch failed for token %s: %s", address, exc)
            self._token_pool_cache[address] = []
            return []

        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        if pairs is None:
            pairs = []

        if not isinstance(pairs, list):
            logger.warning(
                "DEXScreener returned malformed pairs for token %s: %s",
                address,
                type(pairs).__name__,
            )
            pairs = []

        normalized_pairs = [p for p in pairs if isinstance(p, dict)]
        self._token_pool_cache[address] = normalized_pairs
        return normalized_pairs
'''

new_text, count = pattern.subn(replacement, text, count=1)
if count == 0:
    print("[WARN] Could not replace _fetch_live_pairs_for_token automatically.")
else:
    text = new_text
    print("[PATCH] Gated _fetch_live_pairs_for_token behind USE_DEXSCREENER=false")

path.write_text(text, encoding="utf-8", newline="\n")

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
print("[DONE] DEXScreener disabled by default")
