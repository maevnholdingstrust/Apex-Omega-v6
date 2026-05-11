# apex_omega_core/core/venue_registry.py

from enum import Enum
from dataclasses import dataclass
from eth_utils import keccak, to_checksum_address


CHAIN_ID_POLYGON = 137


class PoolFamily(str, Enum):
    V2_CPMM = "V2_CPMM"
    V3_CLMM = "V3_CLMM"
    ALGEBRA_CLMM = "ALGEBRA_CLMM"
    CURVE_STABLE = "CURVE_STABLE"
    BALANCER_WEIGHTED = "BALANCER_WEIGHTED"
    AGGREGATOR = "AGGREGATOR"
    UNKNOWN = "UNKNOWN"


class VenueKind(str, Enum):
    FACTORY = "FACTORY"
    ROUTER = "ROUTER"
    AGGREGATOR = "AGGREGATOR"
    VAULT = "VAULT"
    QUOTER = "QUOTER"


class VenueId(str, Enum):
    QUICKSWAP_ROUTER = "QUICKSWAP_ROUTER"
    DFYN_ROUTER = "DFYN_ROUTER"
    DODO_ROUTER = "DODO_ROUTER"
    KYBERDMM_ROUTER = "KYBERDMM_ROUTER"
    APE_SWAP_ROUTER = "APE_SWAP_ROUTER"
    WAULT_SWAP_ROUTER = "WAULT_SWAP_ROUTER"
    JET_SWAP_ROUTER = "JET_SWAP_ROUTER"
    POLYCAT_ROUTER = "POLYCAT_ROUTER"
    POLYDEX_ROUTER = "POLYDEX_ROUTER"
    COMETH_ROUTER = "COMETH_ROUTER"
    ELK_ROUTER = "ELK_ROUTER"

    ODOS_ROUTER = "ODOS_ROUTER"
    ZRX_EXCHANGE_PROXY = "ZRX_EXCHANGE_PROXY"
    OPENOCEAN_ROUTER = "OPENOCEAN_ROUTER"
    MATCHA_AGGREGATOR = "MATCHA_AGGREGATOR"
    FIREBIRD_ROUTER = "FIREBIRD_ROUTER"
    BEBOP_ROUTER = "BEBOP_ROUTER"
    WOOFI_ROUTER = "WOOFI_ROUTER"

    BALANCER_VAULT = "BALANCER_VAULT"
    AAVE_V3_POOL = "AAVE_V3_POOL"


@dataclass(frozen=True)
class VenueDNA:
    venue_id: VenueId
    kind: VenueKind
    family: PoolFamily
    address: str
    chain_id: int
    dna: str


def make_dna(
    venue_id: VenueId,
    kind: VenueKind,
    family: PoolFamily,
    address: str,
    chain_id: int = CHAIN_ID_POLYGON,
) -> str:
    checksum = to_checksum_address(address)
    preimage = f"{chain_id}:{venue_id.value}:{kind.value}:{family.value}:{checksum}".encode()
    return "0x" + keccak(preimage).hex()


def venue(
    venue_id: VenueId,
    kind: VenueKind,
    family: PoolFamily,
    address: str,
) -> VenueDNA:
    checksum = to_checksum_address(address)
    return VenueDNA(
        venue_id=venue_id,
        kind=kind,
        family=family,
        address=checksum,
        chain_id=CHAIN_ID_POLYGON,
        dna=make_dna(venue_id, kind, family, checksum),
    )


VENUE_REGISTRY: dict[VenueId, VenueDNA] = {
    VenueId.QUICKSWAP_ROUTER: venue(
        VenueId.QUICKSWAP_ROUTER,
        VenueKind.ROUTER,
        PoolFamily.V2_CPMM,
        "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
    ),

    VenueId.DFYN_ROUTER: venue(
        VenueId.DFYN_ROUTER,
        VenueKind.ROUTER,
        PoolFamily.V2_CPMM,
        "0xA102072A4C07F06EC3B4900FDC4C7B80FbbdC5C7",
    ),

    VenueId.DODO_ROUTER: venue(
        VenueId.DODO_ROUTER,
        VenueKind.ROUTER,
        PoolFamily.V2_CPMM,
        "0xa356867fDCEa8e71AEaF87805808803806231FdC",
    ),

    VenueId.KYBERDMM_ROUTER: venue(
        VenueId.KYBERDMM_ROUTER,
        VenueKind.ROUTER,
        PoolFamily.V2_CPMM,
        "0x546C79662E028B661dFB4767664d0273184E4Dd1",
    ),

    VenueId.APE_SWAP_ROUTER: venue(
        VenueId.APE_SWAP_ROUTER,
        VenueKind.ROUTER,
        PoolFamily.V2_CPMM,
        "0xC0788A3aD43d79aa53B09c2EaCc313A787d1d607",
    ),

    VenueId.ODOS_ROUTER: venue(
        VenueId.ODOS_ROUTER,
        VenueKind.AGGREGATOR,
        PoolFamily.AGGREGATOR,
        "0xA7d50a5fC58b23E9C3C40b8C8C856b63a2b38dC5",
    ),

    VenueId.ZRX_EXCHANGE_PROXY: venue(
        VenueId.ZRX_EXCHANGE_PROXY,
        VenueKind.AGGREGATOR,
        PoolFamily.AGGREGATOR,
        "0xDEF1ABE32c034e558Cdd535791643C58a13aCC10",
    ),

    VenueId.OPENOCEAN_ROUTER: venue(
        VenueId.OPENOCEAN_ROUTER,
        VenueKind.AGGREGATOR,
        PoolFamily.AGGREGATOR,
        "0x6352a56caadC4F1E25CD6c75970Fa768A3304e64",
    ),

    VenueId.MATCHA_AGGREGATOR: venue(
        VenueId.MATCHA_AGGREGATOR,
        VenueKind.AGGREGATOR,
        PoolFamily.AGGREGATOR,
        "0x1111111254EEB25477B68fb85Ed929f73A960582",
    ),

    VenueId.BALANCER_VAULT: venue(
        VenueId.BALANCER_VAULT,
        VenueKind.VAULT,
        PoolFamily.BALANCER_WEIGHTED,
        "0xbA1333333333a1BA1108E8412f11850A5C319bA9",
    ),

    VenueId.AAVE_V3_POOL: venue(
        VenueId.AAVE_V3_POOL,
        VenueKind.VAULT,
        PoolFamily.UNKNOWN,
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    ),
}


def get_venue(venue_id: VenueId) -> VenueDNA:
    return VENUE_REGISTRY[venue_id]


def assert_known_venue(address: str) -> VenueDNA:
    checksum = to_checksum_address(address)

    for item in VENUE_REGISTRY.values():
        if item.address == checksum:
            return item

    raise ValueError(f"UNKNOWN_VENUE_REJECTED: {checksum}")


def is_known_router(address: str) -> bool:
    try:
        item = assert_known_venue(address)
        return item.kind in {
            VenueKind.ROUTER,
            VenueKind.AGGREGATOR,
            VenueKind.VAULT,
        }
    except ValueError:
        return False