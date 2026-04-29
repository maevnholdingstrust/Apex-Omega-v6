"""Canonical contract target addresses for strategy execution."""

import os

C1_TARGET = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
C2_TARGET = "0x0466759822ABAA7E416276E1cf2b538d7FC540BD"

# ---------------------------------------------------------------------------
# Polygon Tier 2 Routers — Static Registry (Section 7)
# ---------------------------------------------------------------------------
# Router addresses for V2-compatible Tier-2 AMMs on Polygon mainnet.
# Values are read from the environment first (set via .env or CI variables);
# hardcoded checksummed addresses are used as fallbacks so the module is
# fully usable in environments where .env has not been loaded.

KYBERDMM_ROUTER: str = os.getenv(
    "KYBERDMM_ROUTER", "0x546C79662E028B661dFB4767664d0273184E4Dd1"
)
DODO_ROUTER: str = os.getenv(
    "DODO_ROUTER", "0xa356867fDCEa8e71AEaF87805808803806231FdC"
)
APE_SWAP_ROUTER: str = os.getenv(
    "APE_SWAP_ROUTER", "0xC0788A3aD43d79aa53B09c2EaCc313A787d1d607"
)
WAULT_SWAP_ROUTER: str = os.getenv(
    "WAULT_SWAP_ROUTER", "0x9A17f09C9F7F04428eF5A6B59f2eCf902B9Ff8e4"
)
SYNAPSE_ROUTER: str = os.getenv(
    "SYNAPSE_ROUTER", "0x44F4A35eAaE42Fd2a881Dd301DeedDa9CdfE5b87"
)
IRON_SWAP_ROUTER: str = os.getenv(
    "IRON_SWAP_ROUTER", "0xEAF42d8f61b92b2EAd8e9f3990C97b03265b18D5"
)
DFYN_ROUTER: str = os.getenv(
    "DFYN_ROUTER", "0xA102072A4C07F06EC3B4900FDC4C7B80FbbdC5C7"
)
JET_SWAP_ROUTER: str = os.getenv(
    "JET_SWAP_ROUTER", "0x313C53BCA1df6AA2a80C1aD4781d6A46E0D8f221"
)
POLYCAT_ROUTER: str = os.getenv(
    "POLYCAT_ROUTER", "0x94930a328162957FF1dd48900aF67B5439336cBD"
)
POLYDEX_ROUTER: str = os.getenv(
    "POLYDEX_ROUTER", "0x8C98E2aC57C30f5c03A5Fa53d3eAEb3F79a7A3bC"
)
COMETH_ROUTER: str = os.getenv(
    "COMETH_ROUTER", "0x93B88C3d1E6b7BCE0C8bA8054d5081B6e6fEdEd3"
)
HYPERDEX_ROUTER: str = os.getenv(
    "HYPERDEX_ROUTER", "0x7D3b211D6dE2b0453B61b83C9fC9B97278dEabC9"
)
ELK_ROUTER: str = os.getenv(
    "ELK_ROUTER", "0x9E2A10A9b83Df57bA067C2D3e7d79bdb3B516C5B"
)
JET_SWAP_V2_ROUTER: str = os.getenv(
    "JET_SWAP_V2_ROUTER", "0x0c7A8579f06C0590D54c8FA7dCbCEc96cA2e0B4b"
)
QUICKSWAP_ROUTER: str = os.getenv(
    "QUICKSWAP_ROUTER", "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
)

# Convenience mapping: dex_family → router address for all Tier-2 venues.
TIER2_ROUTER_REGISTRY: dict = {
    "kyberdmm":    KYBERDMM_ROUTER,
    "dodo":        DODO_ROUTER,
    "apeswap":     APE_SWAP_ROUTER,
    "waultswap":   WAULT_SWAP_ROUTER,
    "synapse":     SYNAPSE_ROUTER,
    "ironswap":    IRON_SWAP_ROUTER,
    "dfyn":        DFYN_ROUTER,
    "jetswap":     JET_SWAP_ROUTER,
    "polycat":     POLYCAT_ROUTER,
    "polydex":     POLYDEX_ROUTER,
    "cometh":      COMETH_ROUTER,
    "hyperdex":    HYPERDEX_ROUTER,
    "elk":         ELK_ROUTER,
    "jetswap_v2":  JET_SWAP_V2_ROUTER,
    "quickswap":   QUICKSWAP_ROUTER,
}

# ---------------------------------------------------------------------------
# Polygon Aggregators — Static Registry
# ---------------------------------------------------------------------------
# Aggregator/meta-swap router addresses on Polygon mainnet.

FIREBIRD_ROUTER: str = os.getenv(
    "FIREBIRD_ROUTER", "0xFf7B995e8cA26De1Bd6C768E8d3b96946F72693E"
)
BEBOP_ROUTER: str = os.getenv(
    "BEBOP_ROUTER", "0x6EAdA784C4E5bAE2AEEb1cBf087c05bB5bE2E304"
)
SWAPR_ROUTER: str = os.getenv(
    "SWAPR_ROUTER", "0x78b77FfBbC5B57aD016f7F1fC364D7E1C6Ab3B4e"
)
CAMELOT_ROUTER: str = os.getenv(
    "CAMELOT_ROUTER", "0x1aB1E8E7A97790345e94b807b6E6cb57D6E89E3C"
)
ZRX_EXCHANGE_PROXY: str = os.getenv(
    "ZRX_EXCHANGE_PROXY", "0xDEF1ABE32c034e558Cdd535791643C58a13aCC10"
)
ODOS_ROUTER: str = os.getenv(
    "ODOS_ROUTER", "0xA7d50a5fC58b23E9C3C40b8C8C856b63a2b38dC5"
)
OPENOCEAN_ROUTER: str = os.getenv(
    "OPENOCEAN_ROUTER", "0x6352a56caadC4F1E25CD6c75970Fa768A3304e64"
)
MATCHA_AGGREGATOR: str = os.getenv(
    "MATCHA_AGGREGATOR", "0x1111111254EEB25477B68fb85Ed929f73A960582"
)
WOOFI_ROUTER: str = os.getenv(
    "WOOFI_ROUTER", "0x56d8aB6E4C708B40828b9DaaabF62c22bC4e46F5"
)
TETU_SWAP_ROUTER: str = os.getenv(
    "TETU_SWAP_ROUTER", "0x5E1C5D8B95D9F77e8fD364e5E3Ce6146bAb3C16A"
)
CRODEFISWAP_ROUTER: str = os.getenv(
    "CRODEFISWAP_ROUTER", "0xBb4A8E3f279C6EBB263A7c2B2b5E2056CB2F0E22"
)

# Convenience mapping: aggregator_name → router address.
AGGREGATOR_REGISTRY: dict = {
    "firebird":      FIREBIRD_ROUTER,
    "bebop":         BEBOP_ROUTER,
    "swapr":         SWAPR_ROUTER,
    "camelot":       CAMELOT_ROUTER,
    "0x":            ZRX_EXCHANGE_PROXY,
    "odos":          ODOS_ROUTER,
    "openocean":     OPENOCEAN_ROUTER,
    "matcha":        MATCHA_AGGREGATOR,
    "woofi":         WOOFI_ROUTER,
    "tetu":          TETU_SWAP_ROUTER,
    "crodefiswap":   CRODEFISWAP_ROUTER,
}