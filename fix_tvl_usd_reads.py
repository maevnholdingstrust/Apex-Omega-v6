from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys, re

ROOT = Path.cwd()
CORE = ROOT / "python" / "apex_omega_core" / "core"
ARB = CORE / "polygon_arbitrage.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p):
    b = p.with_suffix(p.suffix + f".bak_tvl_usd_{STAMP}")
    shutil.copy2(p, b)
    print(f"[BACKUP] {b}")

def compile_py(p):
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
    print(f"[OK] compiled {p}")

backup(ARB)
text = ARB.read_text(encoding="utf-8", errors="replace")

# Add helper methods before _pool_from_onchain_v2.
if "def _token_decimals_for_tvl" not in text:
    insert_at = text.find("    def _pool_from_onchain_v2(")
    if insert_at == -1:
        raise SystemExit("[FAIL] _pool_from_onchain_v2 not found")

    helpers = r'''
    def _token_decimals_for_tvl(self, token_address: str) -> int:
        meta = getattr(self, "token_metadata", {}) or {}
        key = token_address.lower()
        item = meta.get(key) or meta.get(token_address)
        if isinstance(item, dict):
            try:
                return int(item.get("decimals", 18))
            except Exception:
                return 18
        if hasattr(item, "decimals"):
            try:
                return int(item.decimals)
            except Exception:
                return 18
        return 18

    def _token_usd_price_for_tvl(self, token_address: str) -> float | None:
        """Return USD price for known tokens. Unknown = None, not zero."""
        meta = getattr(self, "token_metadata", {}) or {}
        key = token_address.lower()
        item = meta.get(key) or meta.get(token_address)

        for field in ("price_usd", "usd_price", "price", "derived_usd"):
            if isinstance(item, dict) and item.get(field) is not None:
                try:
                    v = float(item.get(field))
                    return v if v > 0 else None
                except Exception:
                    pass
            if hasattr(item, field):
                try:
                    v = float(getattr(item, field))
                    return v if v > 0 else None
                except Exception:
                    pass

        # Polygon canonical/common stable + blue-chip fallback anchors.
        anchors = {
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 1.0, # USDC.e
            "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": 1.0, # native USDC
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 1.0, # USDT
            "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": 1.0, # DAI
        }
        return anchors.get(key)

    def _compute_pool_tvl_usd_from_reserves(
        self,
        token0: str,
        token1: str,
        reserve0_raw: int,
        reserve1_raw: int,
    ) -> tuple[float, bool, dict]:
        d0 = self._token_decimals_for_tvl(token0)
        d1 = self._token_decimals_for_tvl(token1)

        r0 = float(reserve0_raw) / float(10 ** d0)
        r1 = float(reserve1_raw) / float(10 ** d1)

        p0 = self._token_usd_price_for_tvl(token0)
        p1 = self._token_usd_price_for_tvl(token1)

        side0 = r0 * p0 if p0 is not None else None
        side1 = r1 * p1 if p1 is not None else None

        if side0 is not None and side1 is not None:
            tvl = side0 + side1
            verified = True
        elif side0 is not None:
            tvl = side0 * 2.0
            verified = False
        elif side1 is not None:
            tvl = side1 * 2.0
            verified = False
        else:
            tvl = 0.0
            verified = False

        return tvl, verified, {
            "token0_decimals": d0,
            "token1_decimals": d1,
            "token0_amount": r0,
            "token1_amount": r1,
            "token0_usd": p0,
            "token1_usd": p1,
            "side0_usd": side0,
            "side1_usd": side1,
        }

'''
    text = text[:insert_at] + helpers + text[insert_at:]
    print("[PATCH] Added TVL USD helpers")

# Replace hardcoded tvl_usd 0 in converter.
old = '''        meta = {
            "address": raw.pair_address,
'''
if old not in text:
    raise SystemExit("[FAIL] converter meta block not found")

if "tvl_verified, tvl_breakdown" not in text:
    text = text.replace(
        old,
        '''        tvl_usd, tvl_verified, tvl_breakdown = self._compute_pool_tvl_usd_from_reserves(
            raw.token0,
            raw.token1,
            raw.reserve0,
            raw.reserve1,
        )

        meta = {
            "address": raw.pair_address,
''',
        1
    )

text = text.replace('"tvl_usd": 0.0,', '"tvl_usd": tvl_usd,')
text = text.replace('"liquidity_usd": 0.0,', '"liquidity_usd": tvl_usd,')

# Add metadata fields.
if '"tvl_verified": tvl_verified,' not in text:
    text = text.replace(
        '"source": classified.source,',
        '"source": classified.source,\n            "tvl_verified": tvl_verified,\n            "tvl_breakdown": tvl_breakdown,',
        1
    )

ARB.write_text(text, encoding="utf-8", newline="\n")
compile_py(ARB)

print("[DONE] TVL USD patch installed.")
