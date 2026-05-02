from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys

ROOT = Path.cwd()
path = ROOT / "python" / "apex_omega_core" / "core" / "polygon_arbitrage.py"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

backup = path.with_suffix(path.suffix + f".bak_seed_prices_{stamp}")
shutil.copy2(path, backup)
print(f"[BACKUP] {backup}")

text = path.read_text(encoding="utf-8", errors="replace")

if "POLYGON_CANONICAL_TOKEN_METADATA" not in text:
    insert = r'''
POLYGON_CANONICAL_TOKEN_METADATA = {
    # stables
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": {"symbol": "USDC.e", "decimals": 6, "price_usd": 1.0},
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": {"symbol": "USDC", "decimals": 6, "price_usd": 1.0},
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": {"symbol": "USDT", "decimals": 6, "price_usd": 1.0},
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": {"symbol": "DAI", "decimals": 18, "price_usd": 1.0},
    "0x45c32fa6df82ead1e2ef74d17b76547eddfaff89": {"symbol": "FRAX", "decimals": 18, "price_usd": 1.0},

    # majors: prices are fallbacks only; update via live feed later
    "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": {"symbol": "WETH", "decimals": 18, "price_usd": 3000.0},
    "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": {"symbol": "WBTC", "decimals": 8, "price_usd": 65000.0},
    "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": {"symbol": "WPOL", "decimals": 18, "price_usd": 0.70},
    "0x0000000000000000000000000000000000001010": {"symbol": "POL", "decimals": 18, "price_usd": 0.70},
    "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39": {"symbol": "LINK", "decimals": 18, "price_usd": 15.0},
    "0x172370d5cd63279efa6d502dab29171933a610af": {"symbol": "CRV", "decimals": 18, "price_usd": 0.30},
    "0x831753dd7087cac61ab5644b308642cc1c33dc13": {"symbol": "QUICK", "decimals": 18, "price_usd": 0.04},
}

'''
    # Insert after imports/logger area near top.
    marker = "logger = logging.getLogger(__name__)"
    if marker in text:
        text = text.replace(marker, marker + "\n" + insert, 1)
    else:
        text = insert + text
    print("[PATCH] Added canonical Polygon token metadata")

if "def _seed_canonical_token_metadata" not in text:
    idx = text.find("    async def refresh_market_registry")
    if idx == -1:
        idx = text.find("    def _token_decimals_for_tvl")
    if idx == -1:
        raise SystemExit("[FAIL] could not find insertion point")

    methods = r'''
    def _seed_canonical_token_metadata(self) -> None:
        """Seed known Polygon token metadata so TVL has sane USD anchors."""
        current = getattr(self, "token_metadata", {}) or {}
        for addr, meta in POLYGON_CANONICAL_TOKEN_METADATA.items():
            existing = current.get(addr.lower()) or {}
            merged = dict(meta)
            if isinstance(existing, dict):
                merged.update({k: v for k, v in existing.items() if v not in (None, "", 0, 0.0)})
                # Keep stable anchors fixed at 1.0 if existing bad price is missing/zero.
                if merged.get("symbol", "").upper() in {"USDC", "USDC.E", "USDT", "DAI", "FRAX"}:
                    merged["price_usd"] = 1.0
            current[addr.lower()] = merged
        self.token_metadata = current

'''
    text = text[:idx] + methods + text[idx:]
    print("[PATCH] Added _seed_canonical_token_metadata")

# Call seed in __init__ after token_metadata exists.
if "self._seed_canonical_token_metadata()" not in text:
    target = "self.token_metadata"
    pos = text.find(target)
    if pos != -1:
        # find end of line
        line_end = text.find("\n", pos)
        text = text[:line_end+1] + "        self._seed_canonical_token_metadata()\n" + text[line_end+1:]
        print("[PATCH] Seed called after token_metadata init")
    else:
        print("[WARN] token_metadata init not found; seed method added only")

# Also seed at start/end of refresh_market_registry to survive overwrite.
if "await self.refresh_market_registry" not in text:
    pass

refresh_marker = "async def refresh_market_registry"
if refresh_marker in text and "self._seed_canonical_token_metadata()  # apex seed before registry merge" not in text:
    text = text.replace(
        "    async def refresh_market_registry",
        "    async def refresh_market_registry",
        1,
    )
    # Insert after function signature line block by finding first newline after def line.
    lines = text.splitlines()
    out = []
    inserted = False
    in_refresh = False
    for line in lines:
        out.append(line)
        if line.startswith("    async def refresh_market_registry"):
            in_refresh = True
            continue
        if in_refresh and not inserted and line.startswith("        "):
            out.append("        self._seed_canonical_token_metadata()  # apex seed before registry merge")
            inserted = True
            in_refresh = False
    text = "\n".join(out) + "\n"
    print("[PATCH] Seed before registry refresh body")

path.write_text(text, encoding="utf-8", newline="\n")

r = subprocess.run([sys.executable, "-m", "py_compile", str(path)], cwd=str(ROOT), capture_output=True, text=True)
if r.returncode:
    print(r.stderr)
    raise SystemExit(r.returncode)

print("[OK] canonical token metadata seeded")
