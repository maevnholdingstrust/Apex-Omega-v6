from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys
import re

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
CORE = ROOT / "python" / "apex_omega_core" / "core"
ARB = CORE / "polygon_arbitrage.py"

def backup(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak_math_registry_{STAMP}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] {path} -> {bak}")

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup(path)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"[WRITE] {path}")

pool_math_registry = r'''
"""
Apex pool math registry.

Purpose:
- classify every pool by protocol family and math mode
- prevent V2 math from being applied to V3 / Curve / Balancer pools
- provide a single execution-support gate before C1/C2 payload generation

Policy:
- discovery may observe any pool
- execution may only consume pools with supported quote + calldata engines
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PoolFamily(str, Enum):
    V2_CPMM = "v2_cpmm"
    V3_CLMM = "v3_clmm"
    ALGEBRA_CLMM = "algebra_clmm"
    CURVE_STABLE = "curve_stable"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    UNKNOWN = "unknown"


class MathMode(str, Enum):
    RESERVE_CPMM = "reserve_cpmm"
    TICK_CLMM = "tick_clmm"
    ALGEBRA_TICK_CLMM = "algebra_tick_clmm"
    CURVE_STABLESWAP = "curve_stableswap"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PoolMathProfile:
    dex_name: str
    factory_address: str
    pool_family: PoolFamily
    math_mode: MathMode
    router_type: str
    default_fee_bps: int | None
    quote_engine: str
    calldata_engine: str
    execution_supported: bool
    notes: str = ""


@dataclass(frozen=True)
class ClassifiedPool:
    chain_id: int
    dex_name: str
    factory_address: str | None
    pool_address: str
    token0: str
    token1: str
    pool_family: PoolFamily
    math_mode: MathMode
    router_type: str
    fee_bps: int | None
    fee_tier: int | None
    reserve0: int | None = None
    reserve1: int | None = None
    sqrt_price_x96: int | None = None
    tick: int | None = None
    liquidity: int | None = None
    execution_supported: bool = False
    quote_engine: str = "unsupported"
    calldata_engine: str = "unsupported"
    source: str = "unknown"


# Polygon factory registry. Add/adjust addresses as repo config matures.
POLYGON_POOL_MATH_REGISTRY: dict[str, PoolMathProfile] = {
    # V2 CPMM
    "0x5757371414417b8c6caad45baef941abc7d3ab32": PoolMathProfile(
        dex_name="quickswap_v2",
        factory_address="0x5757371414417b8c6caad45baef941abc7d3ab32",
        pool_family=PoolFamily.V2_CPMM,
        math_mode=MathMode.RESERVE_CPMM,
        router_type="uniswap_v2_router",
        default_fee_bps=30,
        quote_engine="v2_cpmm",
        calldata_engine="uniswap_v2",
        execution_supported=True,
        notes="QuickSwap V2 factory",
    ),
    "0xc35dadb65012ec5796536bd9864ed8773abc74c4": PoolMathProfile(
        dex_name="sushiswap_v2",
        factory_address="0xc35dadb65012ec5796536bd9864ed8773abc74c4",
        pool_family=PoolFamily.V2_CPMM,
        math_mode=MathMode.RESERVE_CPMM,
        router_type="uniswap_v2_router",
        default_fee_bps=30,
        quote_engine="v2_cpmm",
        calldata_engine="uniswap_v2",
        execution_supported=True,
        notes="SushiSwap Polygon V2 factory",
    ),
    "0xcf083be4164828f00cae704ec15a36d711491284": PoolMathProfile(
        dex_name="apeswap_v2",
        factory_address="0xcf083be4164828f00cae704ec15a36d711491284",
        pool_family=PoolFamily.V2_CPMM,
        math_mode=MathMode.RESERVE_CPMM,
        router_type="uniswap_v2_router",
        default_fee_bps=20,
        quote_engine="v2_cpmm",
        calldata_engine="uniswap_v2",
        execution_supported=True,
        notes="ApeSwap V2-style factory",
    ),
    "0xe7fb3e833efe5f9c441105eb65ef8b261266423b": PoolMathProfile(
        dex_name="dfyn_v2",
        factory_address="0xe7fb3e833efe5f9c441105eb65ef8b261266423b",
        pool_family=PoolFamily.V2_CPMM,
        math_mode=MathMode.RESERVE_CPMM,
        router_type="uniswap_v2_router",
        default_fee_bps=30,
        quote_engine="v2_cpmm",
        calldata_engine="uniswap_v2",
        execution_supported=True,
        notes="Dfyn V2-style factory",
    ),
    "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb": PoolMathProfile(
        dex_name="jetswap_v2",
        factory_address="0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb",
        pool_family=PoolFamily.V2_CPMM,
        math_mode=MathMode.RESERVE_CPMM,
        router_type="uniswap_v2_router",
        default_fee_bps=30,
        quote_engine="v2_cpmm",
        calldata_engine="uniswap_v2",
        execution_supported=True,
        notes="JetSwap V2-style factory",
    ),

    # V3 / CLMM placeholders. Execution remains false until tick math + calldata pass fork sim.
    "0x1f98431c8ad98523631ae4a59f267346ea31f984": PoolMathProfile(
        dex_name="uniswap_v3",
        factory_address="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        pool_family=PoolFamily.V3_CLMM,
        math_mode=MathMode.TICK_CLMM,
        router_type="uniswap_v3_router",
        default_fee_bps=None,
        quote_engine="v3_tick_math",
        calldata_engine="uniswap_v3",
        execution_supported=False,
        notes="V3 requires slot0/liquidity/tick traversal",
    ),

    # Curve/Balancer placeholders. Use registry/vault adapters later.
    "curve_registry": PoolMathProfile(
        dex_name="curve",
        factory_address="curve_registry",
        pool_family=PoolFamily.CURVE_STABLE,
        math_mode=MathMode.CURVE_STABLESWAP,
        router_type="curve_router",
        default_fee_bps=None,
        quote_engine="curve_stableswap",
        calldata_engine="curve",
        execution_supported=False,
        notes="Curve StableSwap adapter required",
    ),
    "balancer_vault": PoolMathProfile(
        dex_name="balancer",
        factory_address="balancer_vault",
        pool_family=PoolFamily.BALANCER_WEIGHTED,
        math_mode=MathMode.BALANCER_WEIGHTED,
        router_type="balancer_vault",
        default_fee_bps=None,
        quote_engine="balancer_weighted",
        calldata_engine="balancer",
        execution_supported=False,
        notes="Balancer vault adapter required",
    ),
}


def normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().lower()
    if value.startswith("0x") and len(value) == 42:
        return value
    return value


def get_pool_math_profile(factory_address: str | None, dex_name: str | None = None) -> PoolMathProfile:
    key = normalize_address(factory_address)
    if key and key in POLYGON_POOL_MATH_REGISTRY:
        return POLYGON_POOL_MATH_REGISTRY[key]

    name = (dex_name or "").lower()
    for profile in POLYGON_POOL_MATH_REGISTRY.values():
        if profile.dex_name.lower() == name or profile.dex_name.lower().startswith(name):
            return profile

    return PoolMathProfile(
        dex_name=dex_name or "unknown",
        factory_address=factory_address or "unknown",
        pool_family=PoolFamily.UNKNOWN,
        math_mode=MathMode.UNKNOWN,
        router_type="unknown",
        default_fee_bps=None,
        quote_engine="unsupported",
        calldata_engine="unsupported",
        execution_supported=False,
        notes="Unclassified pool; discovery-only",
    )


def execution_allowed_for_profile(profile: PoolMathProfile) -> bool:
    return bool(profile.execution_supported)


def classify_pool_kwargs(
    *,
    chain_id: int,
    dex_name: str,
    factory_address: str | None,
    pool_address: str,
    token0: str,
    token1: str,
    reserve0: int | None = None,
    reserve1: int | None = None,
    fee_bps: int | None = None,
    fee_tier: int | None = None,
    sqrt_price_x96: int | None = None,
    tick: int | None = None,
    liquidity: int | None = None,
    source: str = "unknown",
) -> ClassifiedPool:
    profile = get_pool_math_profile(factory_address, dex_name)
    resolved_fee_bps = fee_bps if fee_bps is not None else profile.default_fee_bps

    return ClassifiedPool(
        chain_id=chain_id,
        dex_name=profile.dex_name or dex_name,
        factory_address=factory_address,
        pool_address=pool_address,
        token0=token0,
        token1=token1,
        pool_family=profile.pool_family,
        math_mode=profile.math_mode,
        router_type=profile.router_type,
        fee_bps=resolved_fee_bps,
        fee_tier=fee_tier,
        reserve0=reserve0,
        reserve1=reserve1,
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
        liquidity=liquidity,
        execution_supported=profile.execution_supported,
        quote_engine=profile.quote_engine,
        calldata_engine=profile.calldata_engine,
        source=source,
    )


def require_execution_supported(classified: ClassifiedPool) -> None:
    if not classified.execution_supported:
        raise ValueError(
            f"Unsupported execution math: pool={classified.pool_address} "
            f"family={classified.pool_family} math={classified.math_mode} "
            f"quote={classified.quote_engine} calldata={classified.calldata_engine}"
        )
'''

quote_engines = {
"v2_cpmm_math.py": r'''
from __future__ import annotations

def amount_out_cpmm(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int = 30) -> int:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amount_in_with_fee = amount_in * (10_000 - fee_bps)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    return numerator // denominator
''',
"v3_tick_math.py": r'''
from __future__ import annotations

Q96 = 2 ** 96

class V3TickMathNotImplemented(NotImplementedError):
    pass

def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    raw = (sqrt_price_x96 / Q96) ** 2
    return raw * (10 ** (decimals0 - decimals1))

def quote_v3_exact_input(*args, **kwargs):
    raise V3TickMathNotImplemented("V3 tick traversal quote not implemented yet")
''',
"curve_stableswap_math.py": r'''
from __future__ import annotations

class CurveStableSwapNotImplemented(NotImplementedError):
    pass

def quote_curve_stableswap(*args, **kwargs):
    raise CurveStableSwapNotImplemented("Curve StableSwap invariant quote not implemented yet")
''',
"balancer_weighted_math.py": r'''
from __future__ import annotations

class BalancerWeightedNotImplemented(NotImplementedError):
    pass

def quote_balancer_weighted(*args, **kwargs):
    raise BalancerWeightedNotImplemented("Balancer weighted invariant quote not implemented yet")
''',
"balancer_stable_math.py": r'''
from __future__ import annotations

class BalancerStableNotImplemented(NotImplementedError):
    pass

def quote_balancer_stable(*args, **kwargs):
    raise BalancerStableNotImplemented("Balancer stable invariant quote not implemented yet")
''',
}

write(CORE / "pool_math_registry.py", pool_math_registry)

for filename, content in quote_engines.items():
    write(CORE / filename, content)

if not ARB.exists():
    raise FileNotFoundError(ARB)

backup(ARB)
text = ARB.read_text(encoding="utf-8", errors="replace")

if "from .pool_math_registry import classify_pool_kwargs" not in text:
    marker = "from .onchain_v2_discovery import"
    if marker in text:
        text = text.replace(
            marker,
            "from .pool_math_registry import classify_pool_kwargs, require_execution_supported\n" + marker,
            1,
        )
    else:
        text = "from .pool_math_registry import classify_pool_kwargs, require_execution_supported\n" + text
    print("[PATCH] Added pool math registry import")

# Patch the on-chain converter to classify math. Keep constructor-safe behavior.
pattern = re.compile(
    r'''    def _pool_from_onchain_v2\(self, raw: OnchainV2Pool\) -> Pool:
        .*?
            raise

''',
    re.DOTALL,
)

replacement = r'''    def _pool_from_onchain_v2(self, raw: OnchainV2Pool) -> Pool:
        """Convert on-chain V2 discovery result into the repo Pool model.

        Every pool is classified by factory/pool math profile before it can
        enter C1/C2. Unknown math is discovery-only.
        """
        reserve0 = float(raw.reserve0)
        reserve1 = float(raw.reserve1)

        classified = classify_pool_kwargs(
            chain_id=137,
            dex_name=raw.dex_name,
            factory_address=raw.factory,
            pool_address=raw.pair_address,
            token0=raw.token0,
            token1=raw.token1,
            reserve0=raw.reserve0,
            reserve1=raw.reserve1,
            source="onchain_v2",
        )

        candidates = {
            "address": raw.pair_address,
            "pool_address": raw.pair_address,
            "pair_address": raw.pair_address,

            "dex": classified.dex_name,
            "dex_name": classified.dex_name,

            "token0": raw.token0,
            "token1": raw.token1,

            "reserve0": reserve0,
            "reserve1": reserve1,
            "reserves0": reserve0,
            "reserves1": reserve1,

            "tvl_usd": 0.0,
            "liquidity_usd": 0.0,

            "fee": (classified.fee_bps or 30) / 10_000,
            "fee_bps": classified.fee_bps or 30,
            "fee_tier": classified.fee_tier,

            "pool_type": classified.pool_family.value,
            "math_mode": classified.math_mode.value,
            "router_type": classified.router_type,
            "quote_engine": classified.quote_engine,
            "calldata_engine": classified.calldata_engine,
            "execution_supported": classified.execution_supported,
            "source": classified.source,
        }

        try:
            sig = inspect.signature(Pool)
            allowed = set(sig.parameters.keys())
            kwargs = {k: v for k, v in candidates.items() if k in allowed}
            pool = Pool(**kwargs)
        except TypeError:
            for keys in (
                ("address", "dex", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("address", "dex", "token0", "token1", "reserve0", "reserve1"),
                ("pool_address", "dex_name", "token0", "token1", "reserve0", "reserve1", "tvl_usd"),
                ("pair_address", "dex_name", "token0", "token1", "reserve0", "reserve1"),
            ):
                try:
                    pool = Pool(**{k: candidates[k] for k in keys})
                    break
                except TypeError:
                    continue
            else:
                raise

        # Attach dynamic metadata even if Pool constructor does not define these fields.
        for k, v in candidates.items():
            try:
                setattr(pool, k, v)
            except Exception:
                pass

        return pool

'''

new_text, count = pattern.subn(replacement, text, count=1)
if count:
    text = new_text
    print("[PATCH] Pool converter now attaches math registry metadata")
else:
    print("[WARN] Could not find existing _pool_from_onchain_v2 for registry patch")

# Add execution filter snippet in scan_all_dexes after all_pools creation from onchain if possible.
old = '''                all_pools = [self._pool_from_onchain_v2(p) for p in raw_pools]
                self.last_scan_terminal_state = "POOLS_DISCOVERED" if all_pools else "NO_POOLS_DISCOVERED"
'''
new = '''                all_pools = [self._pool_from_onchain_v2(p) for p in raw_pools]

                # Discovery may observe all supported/unsupported families.
                # Execution must later enforce execution_supported=True.
                # For now we keep all discovered pools but mark math_mode/pool_type explicitly.
                self.last_scan_terminal_state = "POOLS_DISCOVERED" if all_pools else "NO_POOLS_DISCOVERED"
'''
if old in text:
    text = text.replace(old, new)
    print("[PATCH] Added discovery/execution separation note")

ARB.write_text(text, encoding="utf-8", newline="\n")

# Update .env policy.
env_path = ROOT / ".env"
env = env_path.read_text(encoding="utf-8", errors="replace") if env_path.exists() else ""

def set_env(src: str, key: str, value: str) -> str:
    rx = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if rx.search(src):
        return rx.sub(line, src)
    if src and not src.endswith("\n"):
        src += "\n"
    return src + line + "\n"

for key, value in {
    "DISCOVERY_SOURCE": "onchain_v2",
    "USE_DEXSCREENER": "false",
    "ALLOW_V3_EXECUTION": "false",
    "ALLOW_CURVE_EXECUTION": "false",
    "ALLOW_BALANCER_EXECUTION": "false",
    "REQUIRE_POOL_MATH_PROFILE": "true",
}.items():
    env = set_env(env, key, value)

env_path.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env math policy updated")

targets = [
    CORE / "pool_math_registry.py",
    CORE / "v2_cpmm_math.py",
    CORE / "v3_tick_math.py",
    CORE / "curve_stableswap_math.py",
    CORE / "balancer_weighted_math.py",
    CORE / "balancer_stable_math.py",
    ARB,
]

for target in targets:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(target)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[FAIL] {target}")
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)
    print(f"[OK] compiled {target}")

print("[DONE] Pool math registry and individual math-engine scaffolds installed.")
print("")
print("Next boot:")
print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
