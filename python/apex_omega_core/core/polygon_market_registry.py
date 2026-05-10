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
    "AAVE": TokenSpec("AAVE", "0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "LINK": TokenSpec("LINK", "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
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
    "quickswap_v3_algebra": VenueSpec("quickswap_v3_algebra", "algebra", None, None, 5, False, "requires Algebra exactInputSingle adapter"),
}

SUPPORTED_EXECUTION_VENUES = {name: venue for name, venue in VENUES.items() if venue.supported}
CANONICAL_EXECUTION_PAIR = ("USDCe", "WMATIC")
CANONICAL_EXECUTION_ROUTE = ("quickswap_v2", "uniswap_v3")

def token(symbol: str) -> TokenSpec:
    return TOKENS[symbol]

def venue(name: str) -> VenueSpec:
    return VENUES[name]
