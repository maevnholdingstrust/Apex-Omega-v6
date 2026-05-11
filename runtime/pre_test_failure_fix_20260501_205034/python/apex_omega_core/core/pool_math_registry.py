
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
