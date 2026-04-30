from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .multi_market_scanner import MarketQuote, quotes_for_pair
from .polygon_market_registry import TOKENS

STABLES = ("USDCe", "USDC", "USDT", "DAI")


@dataclass(frozen=True)
class GraphHop:
    token_in: str
    token_out: str
    venue: str
    pool: str
    kind: str
    fee_bps: float
    price_out_per_in: float


@dataclass(frozen=True)
class GraphRoute:
    tokens: tuple[str, ...]
    hops: tuple[GraphHop, ...]
    start_amount: float
    final_amount: float
    gross_profit: float
    estimated_cost: float
    net_profit: float
    decision: str


def _quote_edges(a: str, b: str) -> list[GraphHop]:
    edges: list[GraphHop] = []
    quotes_ab = quotes_for_pair(a, b)
    for q in quotes_ab:
        edges.append(GraphHop(a, b, q.venue, q.pool, q.kind, q.fee_bps, q.price_quote_per_base))
        if q.price_quote_per_base > 0:
            edges.append(GraphHop(b, a, q.venue, q.pool, q.kind, q.fee_bps, 1 / q.price_quote_per_base))
    return edges


def build_token_graph(tokens: Iterable[str] | None = None) -> dict[str, list[GraphHop]]:
    symbols = list(tokens or TOKENS.keys())
    graph: dict[str, list[GraphHop]] = {s: [] for s in symbols}
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            for edge in _quote_edges(a, b):
                if edge.token_in in graph:
                    graph[edge.token_in].append(edge)
    return graph


def _dfs_paths(graph: dict[str, list[GraphHop]], start: str, max_hops: int) -> list[tuple[GraphHop, ...]]:
    paths: list[tuple[GraphHop, ...]] = []

    def walk(token: str, route: tuple[GraphHop, ...], visited: set[str]) -> None:
        if len(route) >= max_hops:
            return
        for edge in graph.get(token, []):
            if edge.token_out in visited and edge.token_out not in STABLES:
                continue
            next_route = route + (edge,)
            if edge.token_out in STABLES and edge.token_out != start and len(next_route) >= 2:
                paths.append(next_route)
            if edge.token_out not in STABLES:
                walk(edge.token_out, next_route, visited | {edge.token_out})

    walk(start, tuple(), {start})
    return paths


def evaluate_path(
    hops: tuple[GraphHop, ...],
    start_amount: float,
    gas_cost_usdc: float,
    flash_fee_bps: float,
    risk_buffer_usdc: float,
    mempool_degradation_bps: float,
    min_net_profit_usdc: float,
) -> GraphRoute:
    amount = start_amount
    tokens = [hops[0].token_in]
    for hop in hops:
        amount *= hop.price_out_per_in
        tokens.append(hop.token_out)
    gross = amount - start_amount
    flash_fee = start_amount * (flash_fee_bps / 10_000)
    mempool_degradation = amount * (mempool_degradation_bps / 10_000)
    # Approximate gas scales with route length until per-adapter gas model is wired.
    route_gas = gas_cost_usdc * max(1, len(hops) / 2)
    estimated_cost = route_gas + flash_fee + risk_buffer_usdc + mempool_degradation
    net = gross - estimated_cost
    return GraphRoute(tuple(tokens), hops, start_amount, amount, gross, estimated_cost, net, "STRIKE_CANDIDATE" if net > min_net_profit_usdc else "IDLE")


def find_usdc_value_routes(
    start_stable: str = "USDCe",
    start_amount: float = 100.0,
    max_hops: int = 3,
    max_tokens: int = 9,
    min_net_profit_usdc: float = 0.0,
    gas_cost_usdc: float = 0.55,
    flash_fee_bps: float = 5.0,
    risk_buffer_usdc: float = 0.0,
    mempool_degradation_bps: float = 25.0,
) -> list[GraphRoute]:
    if start_stable not in STABLES:
        raise ValueError("start_stable must be a USD-denominated stable token")
    selected = list(TOKENS.keys())[:max_tokens]
    if start_stable not in selected:
        selected.insert(0, start_stable)
    graph = build_token_graph(selected)
    raw_paths = _dfs_paths(graph, start_stable, max_hops=max_hops)
    routes = [
        evaluate_path(p, start_amount, gas_cost_usdc, flash_fee_bps, risk_buffer_usdc, mempool_degradation_bps, min_net_profit_usdc)
        for p in raw_paths
    ]
    return sorted(routes, key=lambda r: r.net_profit, reverse=True)
