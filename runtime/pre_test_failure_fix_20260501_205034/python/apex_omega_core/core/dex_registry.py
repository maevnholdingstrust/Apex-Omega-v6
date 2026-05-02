
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DexFamily(str, Enum):
    V2_CPMM = "v2_cpmm"
    V3_CLMM = "v3_clmm"
    ALGEBRA_CLMM = "algebra_clmm"
    CURVE_STABLE = "curve_stable"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    AGGREGATOR = "aggregator"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DexDefinition:
    name: str
    family: DexFamily
    factory: str | None
    router: str | None
    fee_bps: int | None
    execution_supported: bool
    notes: str = ""


POLYGON_DEX_REGISTRY: dict[str, DexDefinition] = {
    # V2 CPMM, executable once reserves + calldata are verified
    "quickswap_v2": DexDefinition("quickswap_v2", DexFamily.V2_CPMM, "0x5757371414417b8c6caad45baef941abc7d3ab32", None, 30, True),
    "sushiswap_v2": DexDefinition("sushiswap_v2", DexFamily.V2_CPMM, "0xc35dadb65012ec5796536bd9864ed8773abc74c4", None, 30, True),
    "apeswap_v2": DexDefinition("apeswap_v2", DexFamily.V2_CPMM, "0xcf083be4164828f00cae704ec15a36d711491284", None, 20, True),
    "dfyn_v2": DexDefinition("dfyn_v2", DexFamily.V2_CPMM, "0xe7fb3e833efe5f9c441105eb65ef8b261266423b", None, 30, True),
    "jetswap_v2": DexDefinition("jetswap_v2", DexFamily.V2_CPMM, "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb", None, 30, True),

    # Added candidates. Verify factory/router before enabling execution.
    "comethswap": DexDefinition("comethswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "polycat": DexDefinition("polycat", DexFamily.V2_CPMM, None, None, 30, False, "legacy; verify liquidity"),
    "waultswap": DexDefinition("waultswap", DexFamily.V2_CPMM, None, None, 20, False, "verify factory/router"),
    "firebird": DexDefinition("firebird", DexFamily.V2_CPMM, None, None, 30, False, "verify exact model"),
    "cafeswap": DexDefinition("cafeswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "gravity": DexDefinition("gravity", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "elk": DexDefinition("elk", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "luaswap": DexDefinition("luaswap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "polyzap": DexDefinition("polyzap", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),
    "swapr": DexDefinition("swapr", DexFamily.V2_CPMM, None, None, 30, False, "verify factory/router"),

    # V3 / Algebra: discovery-visible, execution-blocked until tick math + calldata pass fork sim
    "uniswap_v3": DexDefinition("uniswap_v3", DexFamily.V3_CLMM, "0x1f98431c8ad98523631ae4a59f267346ea31f984", None, None, False),
    "quickswap_v3_algebra": DexDefinition("quickswap_v3_algebra", DexFamily.ALGEBRA_CLMM, None, None, None, False, "requires Algebra adapter"),
    "retro": DexDefinition("retro", DexFamily.ALGEBRA_CLMM, None, None, None, False, "verify factory/router"),
    "pearl": DexDefinition("pearl", DexFamily.ALGEBRA_CLMM, None, None, None, False, "verify factory/router"),
    "kyber_elastic": DexDefinition("kyber_elastic", DexFamily.V3_CLMM, None, None, None, False, "separate model may be required"),

    # Curve / Balancer
    "curve": DexDefinition("curve", DexFamily.CURVE_STABLE, None, None, None, False, "requires Curve registry + invariant adapter"),
    "balancer_weighted": DexDefinition("balancer_weighted", DexFamily.BALANCER_WEIGHTED, "balancer_vault", None, None, False),
    "balancer_stable": DexDefinition("balancer_stable", DexFamily.BALANCER_STABLE, "balancer_vault", None, None, False),

    # Aggregators: quote candidates only, not direct DEX pools
    "zero_x": DexDefinition("zero_x", DexFamily.AGGREGATOR, None, None, None, False),
    "oneinch": DexDefinition("oneinch", DexFamily.AGGREGATOR, None, None, None, False),
    "paraswap": DexDefinition("paraswap", DexFamily.AGGREGATOR, None, None, None, False),
    "odos": DexDefinition("odos", DexFamily.AGGREGATOR, None, None, None, False),
    "openocean": DexDefinition("openocean", DexFamily.AGGREGATOR, None, None, None, False),
    "kyber_aggregator": DexDefinition("kyber_aggregator", DexFamily.AGGREGATOR, None, None, None, False),
}


def executable_v2_factories() -> dict[str, str]:
    return {
        name: dex.factory
        for name, dex in POLYGON_DEX_REGISTRY.items()
        if dex.family == DexFamily.V2_CPMM and dex.execution_supported and dex.factory
    }
