from __future__ import annotations

from dataclasses import dataclass

CHAIN_ID = 137

@dataclass(frozen=True)
class TokenSpec:
    symbol: str
    address: str
    decimals: int

@dataclass(frozen=True)
class VenueSpec:
    name: str
    kind: str
    factory: str | None
    router: str | None
    default_fee_bps: int
    supported: bool
    notes: str = ""

TOKENS: dict[str, TokenSpec] = {
    "USDCe": TokenSpec("USDCe", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "USDC": TokenSpec("USDC", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    "USDT": TokenSpec("USDT", "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI": TokenSpec("DAI", "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "WMATIC": TokenSpec("WMATIC", "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH": TokenSpec("WETH", "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC": TokenSpec("WBTC", "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    "FRAX": TokenSpec("FRAX", "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89", 18),
    "MAI": TokenSpec("MAI", "0xa3Fa99A148fA48D14Ed51d610c367C61876997F1", 18),
    "TUSD": TokenSpec("TUSD", "0x2e1AD108fF1D8C782fcBbB89AAd783aC49586756", 18),
    "stMATIC": TokenSpec("stMATIC", "0x3A58a54C066FdC0f2D55FC9C89F0415C92eBf3C4", 18),
    "MaticX": TokenSpec("MaticX", "0xfa68FB4628DFF1028CFEc22b4162FCcd0d45efb6", 18),
    "wstETH": TokenSpec("wstETH", "0x03b54A6e9a984069379fae1a4fC4dBAE93B3bCCD", 18),
    "AAVE": TokenSpec("AAVE", "0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "LINK": TokenSpec("LINK", "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
    "CRV": TokenSpec("CRV", "0x172370d5Cd63279eFa6d502DAB29171933a610AF", 18),
    "BAL": TokenSpec("BAL", "0x9a71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3", 18),
    "SUSHI": TokenSpec("SUSHI", "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a", 18),
    "UNI": TokenSpec("UNI", "0xb33EaAd8d922B1083446DC23f610c2567fB5180f", 18),
    "COMP": TokenSpec("COMP", "0x8505b9d2254A7Ae468c0E9dd10Ccea3A837aef5c", 18),
    "MKR": TokenSpec("MKR", "0x6f7C932e7684666C9fd1d44527765433e01fF61d", 18),
    "SNX": TokenSpec("SNX", "0x50B728D8D964fd00C2d0AAD81718b71311feF68a", 18),
    "GHST": TokenSpec("GHST", "0x385Eeac5cB85A38A9a07A70c73e0a3271CfB54A7", 18),
    "QUICK": TokenSpec("QUICK", "0xB5C064F955D8e7F38fE0460C556a72987494eE17", 18),
    "FXS": TokenSpec("FXS", "0x1a3acf6D19267E2d3e7f898f42803e90C9219062", 18),
    "DPI": TokenSpec("DPI", "0x85955046DF4668e1DD369D2DE9f3AEFC9cD8DA0E", 18),
    "SAND": TokenSpec("SAND", "0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683", 18),
    "MANA": TokenSpec("MANA", "0xA1c57f48F0Deb89f569dFbE6E2B7f46D33606fD4", 18),
    "TEL": TokenSpec("TEL", "0xdf7837de1F2Fa4631D716CF2502f8b230F1dcc32", 2),
    "APE": TokenSpec("APE", "0xB7b31a6BC18e48888545CE79E83E06003be70930", 18),
    "OLAS": TokenSpec("OLAS", "0xfEf5d947472e72Efbb2E388c730B7428406F2F95", 18),
    "TETU": TokenSpec("TETU", "0x255707B70BF90aa112006E1b07B9AeA6De021424", 18),
    "GRT": TokenSpec("GRT", "0x5fe2B58c013d7601147DcdD68C143A77499f5531", 18),
    "VISION": TokenSpec("VISION", "0x034b2090b579228482520c589dbD397c53FC51cC", 18),
}

VENUES: dict[str, VenueSpec] = {
    # Generic UniswapV2-compatible routers/factories.
    "quickswap_v2": VenueSpec("quickswap_v2", "v2", "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32", "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff", 30, True),
    "sushiswap_v2": VenueSpec("sushiswap_v2", "v2", "0xc35DADB65012eC5796536bD9864eD8773aBc74C4", "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506", 30, True),
    "apeswap_v2": VenueSpec("apeswap_v2", "v2", "0xCf083Be4164828f00cAE704EC15a36D711491284", "0xC0788A3aD43d79aa53B09c2EaCc313A787d1d607", 30, True),
    "dfyn_v2": VenueSpec("dfyn_v2", "v2", "0xE7Fb3e833eFE5F9c441105EB65Ef8b261266423B", "0xA8b607Aa09B6A2641cF6F90f643E76d3f6e6Ff73", 30, True),
    "jetswap_v2": VenueSpec("jetswap_v2", "v2", "0x668ad0ed2622b0ac445205f25ee12a7d618cfb52", "0x5c6eBB8ba4bFe04bdeA4eF6c6eBf3eF2cA19E3c2", 30, True, "router address should be verified before live enable"),
    # Generic UniswapV3-compatible.
    "uniswap_v3": VenueSpec("uniswap_v3", "v3", "0x1F98431c8aD98523631AE4a59f267346ea31F984", "0xE592427A0AEce92De3Edee1F18E0157C05861564", 5, True),
    # AMM families that require dedicated calldata adapters before live execution.
    "curve": VenueSpec("curve", "curve", None, None, 4, False, "requires pool-specific exchange calldata"),
    "balancer_v2": VenueSpec("balancer_v2", "balancer", None, "0xBA12222222228d8Ba445958a75a0704d566BF2C8", 0, False, "requires vault swap/swapKind/poolId adapter"),
    "quickswap_v3_algebra": VenueSpec(
        "quickswap_v3_algebra",
        "algebra",
        "0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28",
        "0xf5b509bB0909a69B1c207E495f687a596C168E12",
        5,
        False,
        "QuickSwap Algebra V3; live quoter required before execution",
    ),
}

SUPPORTED_EXECUTION_VENUES = {name: venue for name, venue in VENUES.items() if venue.supported}
CANONICAL_EXECUTION_PAIR = ("USDCe", "WMATIC")
CANONICAL_EXECUTION_ROUTE = ("quickswap_v2", "uniswap_v3")

def token(symbol: str) -> TokenSpec:
    return TOKENS[symbol]

def venue(name: str) -> VenueSpec:
    return VENUES[name]
