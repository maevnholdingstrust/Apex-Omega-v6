from __future__ import annotations

from dataclasses import dataclass

from .multi_hop_graph_router import GraphRoute
from .swap_adapters import encode_route_steps


@dataclass(frozen=True)
class CompiledPayload:
    route_tokens: tuple[str, ...]
    steps_len: int
    calldata: bytes
    execution_ready: bool
    reason: str


def compile_graph_route(route: GraphRoute) -> CompiledPayload:
    if not route.execution_grade or route.decision != "STRIKE_CANDIDATE":
        return CompiledPayload(route.tokens, 0, b"", False, "route not execution grade or not profitable")
    try:
        steps = [
            {
                "token_in": h.token_in,
                "token_out": h.token_out,
                "pool": h.pool,
                "venue": h.venue,
                "kind": h.kind,
            }
            for h in route.hops
        ]
        calldata = encode_route_steps(steps)
        return CompiledPayload(route.tokens, len(steps), calldata, True, "compiled")
    except Exception as exc:
        return CompiledPayload(route.tokens, 0, b"", False, str(exc))
