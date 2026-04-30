from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .polygon_market_registry import TOKENS, SUPPORTED_EXECUTION_VENUES
from .rpc_tester import get_w3

V2_FACTORY_ABI = [{"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"name":"pair","type":"address"}],"stateMutability":"view","type":"function"}]
V2_PAIR_ABI = [
    {"inputs": [], "name": "getReserves", "outputs": [{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}], "stateMutability":"view", "type":"function"},
    {"inputs": [], "name": "token0", "outputs": [{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
    {"inputs": [], "name": "token1", "outputs": [{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
]
V3_FACTORY_ABI = [{"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]
V3_POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [{"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},{"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}], "stateMutability":"view", "type":"function"},
    {"inputs": [], "name": "liquidity", "outputs": [{"name":"","type":"uint128"}], "stateMutability":"view", "type":"function"},
    {"inputs": [], "name": "token0", "outputs": [{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
    {"inputs": [], "name": "token1", "outputs": [{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
]

ZERO = "0x0000000000000000000000000000000000000000"
V3_FEES = [100, 500, 3000, 10000]
STABLES = ["USDCe", "USDC", "USDT", "DAI"]

@dataclass(frozen=True)
class MarketQuote:
    venue: str
    pool: str
    kind: str
    fee_bps: float
    base_symbol: str
    quote_symbol: str
    price_quote_per_base: float
    liquidity_hint: float

@dataclass(frozen=True)
class ScannerOpportunity:
    base_symbol: str
    quote_symbol: str
    buy_venue: str
    sell_venue: str
    buy_pool: str
    sell_pool: str
    buy_price: float
    sell_price: float
    raw_spread_bps: float
    execution_supported: bool

@dataclass(frozen=True)
class UsdcValueRoute:
    start_stable: str
    mid_token: str
    end_stable: str
    buy_venue: str
    sell_venue: str
    buy_pool: str
    sell_pool: str
    start_amount_usdc: float
    mid_amount: float
    final_amount_usdc: float
    gross_profit_usdc: float
    raw_spread_bps: float
    route_supported: bool


def _checksum(address: str) -> str:
    from web3 import Web3
    return Web3.to_checksum_address(address)

def _v3_price_token1_per_token0(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    raw = (sqrt_price_x96 / (2 ** 96)) ** 2
    return raw * (10 ** (dec0 - dec1))

def _decimals(address: str) -> int:
    lower = address.lower()
    for spec in TOKENS.values():
        if spec.address.lower() == lower: return spec.decimals
    return 18

def _symbol(address: str) -> str:
    lower = address.lower()
    for spec in TOKENS.values():
        if spec.address.lower() == lower: return spec.symbol
    return address[:8]

def _fetch_v2_quote(venue_name: str, base: str, quote: str) -> MarketQuote | None:
    venue = SUPPORTED_EXECUTION_VENUES[venue_name]
    if not venue.factory: return None
    w3 = get_w3(); factory = w3.eth.contract(address=_checksum(venue.factory), abi=V2_FACTORY_ABI)
    pair_addr = factory.functions.getPair(_checksum(base), _checksum(quote)).call()
    if not pair_addr or pair_addr.lower() == ZERO: return None
    pair = w3.eth.contract(address=_checksum(pair_addr), abi=V2_PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call(); token0 = pair.functions.token0().call(); token1 = pair.functions.token1().call()
    h0, h1 = r0 / (10 ** _decimals(token0)), r1 / (10 ** _decimals(token1))
    if h0 <= 0 or h1 <= 0: return None
    if token0.lower() == base.lower() and token1.lower() == quote.lower(): price = h1 / h0; liquidity_hint = h1 * 2
    elif token1.lower() == base.lower() and token0.lower() == quote.lower(): price = h0 / h1; liquidity_hint = h0 * 2
    else: return None
    return MarketQuote(venue_name, pair_addr, "v2", venue.default_fee_bps, _symbol(base), _symbol(quote), price, liquidity_hint)

def _fetch_v3_quote(venue_name: str, base: str, quote: str) -> MarketQuote | None:
    venue = SUPPORTED_EXECUTION_VENUES[venue_name]
    if not venue.factory: return None
    w3 = get_w3(); factory = w3.eth.contract(address=_checksum(venue.factory), abi=V3_FACTORY_ABI)
    best = None
    for fee in V3_FEES:
        pool_addr = factory.functions.getPool(_checksum(base), _checksum(quote), fee).call()
        if not pool_addr or pool_addr.lower() == ZERO: continue
        pool = w3.eth.contract(address=_checksum(pool_addr), abi=V3_POOL_ABI); liquidity = pool.functions.liquidity().call()
        if liquidity <= 0: continue
        slot0 = pool.functions.slot0().call(); token0 = pool.functions.token0().call(); token1 = pool.functions.token1().call()
        token1_per_token0 = _v3_price_token1_per_token0(slot0[0], _decimals(token0), _decimals(token1))
        if token0.lower() == base.lower() and token1.lower() == quote.lower(): price = token1_per_token0
        elif token1.lower() == base.lower() and token0.lower() == quote.lower(): price = 1 / token1_per_token0 if token1_per_token0 else 0
        else: continue
        q = MarketQuote(venue_name, pool_addr, "v3", fee / 100, _symbol(base), _symbol(quote), price, float(liquidity))
        if best is None or q.liquidity_hint > best.liquidity_hint: best = q
    return best

def quotes_for_pair(base_symbol: str, quote_symbol: str) -> list[MarketQuote]:
    base, quote = TOKENS[base_symbol].address, TOKENS[quote_symbol].address
    quotes = []
    for venue_name, venue in SUPPORTED_EXECUTION_VENUES.items():
        try:
            q = _fetch_v2_quote(venue_name, base, quote) if venue.kind == "v2" else _fetch_v3_quote(venue_name, base, quote)
            if q and q.price_quote_per_base > 0: quotes.append(q)
        except Exception: continue
    return quotes

def scan_multi_market(max_pairs: int = 24, min_spread_bps: float = 40.0) -> list[ScannerOpportunity]:
    symbols = list(TOKENS.keys()); token_pairs = list(combinations(symbols, 2))[:max_pairs]
    opportunities = []
    for base_symbol, quote_symbol in token_pairs:
        quotes = quotes_for_pair(base_symbol, quote_symbol)
        if len(quotes) < 2: continue
        buy = min(quotes, key=lambda x: x.price_quote_per_base); sell = max(quotes, key=lambda x: x.price_quote_per_base)
        if buy.pool == sell.pool or sell.price_quote_per_base <= buy.price_quote_per_base: continue
        spread_bps = ((sell.price_quote_per_base - buy.price_quote_per_base) / buy.price_quote_per_base) * 10_000
        if spread_bps >= min_spread_bps:
            opportunities.append(ScannerOpportunity(base_symbol, quote_symbol, buy.venue, sell.venue, buy.pool, sell.pool, buy.price_quote_per_base, sell.price_quote_per_base, spread_bps, True))
    return sorted(opportunities, key=lambda o: o.raw_spread_bps, reverse=True)

def scan_usdc_value_routes(start_amount_usdc: float = 100.0, min_profit_usdc: float = 0.0, max_mid_tokens: int = 12) -> list[UsdcValueRoute]:
    mids = [s for s in TOKENS.keys() if s not in STABLES][:max_mid_tokens]
    routes = []
    for stable in [s for s in STABLES if s in TOKENS]:
        for mid in mids:
            quotes = quotes_for_pair(mid, stable)  # price stable per mid
            if len(quotes) < 2: continue
            buy = min(quotes, key=lambda q: q.price_quote_per_base)   # pay stable per mid
            sell = max(quotes, key=lambda q: q.price_quote_per_base)  # receive stable per mid
            mid_amount = start_amount_usdc / buy.price_quote_per_base
            final_usdc = mid_amount * sell.price_quote_per_base
            gross = final_usdc - start_amount_usdc
            spread_bps = ((sell.price_quote_per_base - buy.price_quote_per_base) / buy.price_quote_per_base) * 10_000
            if gross > min_profit_usdc:
                routes.append(UsdcValueRoute(stable, mid, stable, buy.venue, sell.venue, buy.pool, sell.pool, start_amount_usdc, mid_amount, final_usdc, gross, spread_bps, True))
    return sorted(routes, key=lambda r: r.gross_profit_usdc, reverse=True)
