from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .multi_market_scanner import quotes_for_pair
from .polygon_market_registry import TOKENS
from .pool_state_binding import BoundPoolState, bind_v2_pool_state, bind_v3_pool_state, require_bound_execution_grade

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
    liquidity_hint: float
    bound_state: BoundPoolState | None = None

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
    execution_grade: bool
    rejection_reason: str


def _bind_state(edge: GraphHop) -> BoundPoolState | None:
    if edge.kind == "v2":
        return bind_v2_pool_state(edge.pool, edge.token_in, edge.token_out, edge.fee_bps)
    if edge.kind == "v3":
        return bind_v3_pool_state(edge.pool, edge.token_in, edge.token_out, int(edge.fee_bps * 100))
    return None


def _quote_edges(a: str, b: str, bind_state: bool = True) -> list[GraphHop]:
    edges: list[GraphHop] = []
    for q in quotes_for_pair(a, b):
        e1 = GraphHop(a, b, q.venue, q.pool, q.kind, q.fee_bps, q.price_quote_per_base, q.liquidity_hint)
        e2 = GraphHop(b, a, q.venue, q.pool, q.kind, q.fee_bps, 1 / q.price_quote_per_base, q.liquidity_hint) if q.price_quote_per_base > 0 else None
        for edge in [e1, e2]:
            if edge is None:
                continue
            if bind_state:
                bound = _bind_state(edge)
                edge = GraphHop(edge.token_in, edge.token_out, edge.venue, edge.pool, edge.kind, edge.fee_bps, edge.price_out_per_in, edge.liquidity_hint, bound)
            edges.append(edge)
    return edges


def build_token_graph(tokens: Iterable[str] | None = None, bind_state: bool = True) -> dict[str, list[GraphHop]]:
    symbols = list(tokens or TOKENS.keys())
    graph = {s: [] for s in symbols}
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            for edge in _quote_edges(a, b, bind_state=bind_state):
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


def _cpmm_out(amount_in: float, reserve_in: float, reserve_out: float, fee_bps: float) -> float:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    x_eff = amount_in * (1.0 - fee_bps / 10_000.0)
    return (x_eff * reserve_out) / (reserve_in + x_eff)


def _hop_output(hop: GraphHop, amount: float) -> tuple[float, bool, str]:
    if hop.bound_state is None or not hop.bound_state.execution_grade:
        return 0.0, False, "missing execution-grade bound state"
    if hop.kind == "v2":
        st = hop.bound_state.state
        return _cpmm_out(amount, float(st["reserve_in"]), float(st["reserve_out"]), float(st["fee_bps"])), True, "v2 cpmm"
    return 0.0, False, f"{hop.kind} execution-grade math not wired into graph output"


def evaluate_path(hops: tuple[GraphHop, ...], start_amount: float, gas_cost_usdc: float, flash_fee_bps: float, risk_buffer_usdc: float, mempool_degradation_bps: float, min_net_profit_usdc: float) -> GraphRoute:
    tokens = [hops[0].token_in]
    for hop in hops:
        tokens.append(hop.token_out)
    try:
        require_bound_execution_grade([h.bound_state for h in hops if h.bound_state is not None])
    except Exception as exc:
        return GraphRoute(tuple(tokens), hops, start_amount, 0.0, 0.0, 0.0, float("-inf"), "REJECTED", False, str(exc))
    amount = start_amount
    for hop in hops:
        amount, ok, reason = _hop_output(hop, amount)
        if not ok:
            return GraphRoute(tuple(tokens), hops, start_amount, 0.0, 0.0, 0.0, float("-inf"), "REJECTED", False, reason)
    gross = amount - start_amount
    flash_fee = start_amount * (flash_fee_bps / 10_000)
    mempool_degradation = amount * (mempool_degradation_bps / 10_000)
    route_gas = gas_cost_usdc * max(1, len(hops) / 2)
    estimated_cost = route_gas + flash_fee + risk_buffer_usdc + mempool_degradation
    net = gross - estimated_cost
    return GraphRoute(tuple(tokens), hops, start_amount, amount, gross, estimated_cost, net, "STRIKE_CANDIDATE" if net > min_net_profit_usdc else "IDLE", True, "")


def find_usdc_value_routes(start_stable: str = "USDCe", start_amount: float = 100.0, max_hops: int = 3, max_tokens: int = 9, min_net_profit_usdc: float = 0.0, gas_cost_usdc: float = 0.55, flash_fee_bps: float = 9.0, risk_buffer_usdc: float = 0.0, mempool_degradation_bps: float = 25.0, require_execution_grade: bool = True) -> list[GraphRoute]:
    if start_stable not in STABLES:
        raise ValueError("start_stable must be a USD-denominated stable token")
    selected = list(TOKENS.keys())[:max_tokens]
    if start_stable not in selected:
        selected.insert(0, start_stable)
    graph = build_token_graph(selected, bind_state=True)
    raw_paths = _dfs_paths(graph, start_stable, max_hops=max_hops)
    routes = [evaluate_path(p, start_amount, gas_cost_usdc, flash_fee_bps, risk_buffer_usdc, mempool_degradation_bps, min_net_profit_usdc) for p in raw_paths]
    if require_execution_grade:
        routes = [r for r in routes if r.execution_grade]
    return sorted(routes, key=lambda r: r.net_profit, reverse=True)
