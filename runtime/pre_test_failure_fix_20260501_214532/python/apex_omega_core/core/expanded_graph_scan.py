from __future__ import annotations

from dataclasses import dataclass

from .fork_readiness_gate import ForkReadinessReport, fork_simulation_readiness_gate
from .multi_hop_graph_router import GraphRoute, find_usdc_value_routes
from .route_payload_compiler import CompiledPayload, compile_graph_route


@dataclass(frozen=True)
class ExpandedGraphCandidate:
    route: GraphRoute
    payload: CompiledPayload
    fork_readiness: ForkReadinessReport


@dataclass(frozen=True)
class ExpandedGraphScanResult:
    scanned: int
    candidates: tuple[ExpandedGraphCandidate, ...]
    rejected: int


def run_expanded_graph_scan(
    *,
    fork_rpc_url: str | None,
    start_stable: str = "USDCe",
    start_amount: float = 100.0,
    max_hops: int = 3,
    max_tokens: int = 14,
    min_net_profit_usdc: float = 0.0,
    gas_cost_usdc: float = 0.55,
    flash_fee_bps: float = 5.0,
    risk_buffer_usdc: float = 0.0,
    mempool_degradation_bps: float = 25.0,
    top_n: int = 10,
) -> ExpandedGraphScanResult:
    """Run the broad opportunity surface: multi-token, multi-venue, multi-hop.

    This is still fail-closed:
    - Routes must be execution-grade.
    - Payload must compile.
    - Fork readiness must pass before any downstream fork simulation.
    - No signing or broadcasting occurs here.
    """
    routes = find_usdc_value_routes(
        start_stable=start_stable,
        start_amount=start_amount,
        max_hops=max_hops,
        max_tokens=max_tokens,
        min_net_profit_usdc=min_net_profit_usdc,
        gas_cost_usdc=gas_cost_usdc,
        flash_fee_bps=flash_fee_bps,
        risk_buffer_usdc=risk_buffer_usdc,
        mempool_degradation_bps=mempool_degradation_bps,
        require_execution_grade=True,
    )
    candidates: list[ExpandedGraphCandidate] = []
    rejected = 0
    for route in routes:
        payload = compile_graph_route(route)
        readiness = fork_simulation_readiness_gate(
            fork_rpc_url=fork_rpc_url,
            route_execution_grade=route.execution_grade,
            payload=payload.calldata if payload.execution_ready else None,
            expected_profit_usdc=route.net_profit,
        )
        if route.decision == "STRIKE_CANDIDATE" and payload.execution_ready and readiness.ready:
            candidates.append(ExpandedGraphCandidate(route, payload, readiness))
        else:
            rejected += 1
        if len(candidates) >= top_n:
            break
    return ExpandedGraphScanResult(scanned=len(routes), candidates=tuple(candidates), rejected=rejected)
