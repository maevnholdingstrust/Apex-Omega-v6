
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LaneRole(str, Enum):
    V2_DISCOVERY = "v2_discovery"
    V3_DISCOVERY = "v3_discovery"
    CURVE_BALANCER_SYNC = "curve_balancer_sync"
    AGGREGATOR_ENRICHMENT = "aggregator_enrichment"
    FORK_SIMULATION = "fork_simulation"
    C2_RECOMPUTE = "c2_recompute"
    DNA_REDIS_LOGGING = "dna_redis_logging"
    HEALTH_FAILSAFE = "health_failsafe"


@dataclass
class LaneAssignment:
    lane_id: int
    role: LaneRole
    description: str
    queue_name: str
    max_inflight: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


def default_32_lane_plan() -> list[LaneAssignment]:
    lanes: list[LaneAssignment] = []

    for i in range(1, 9):
        lanes.append(LaneAssignment(i, LaneRole.V2_DISCOVERY, "V2 CPMM reserves + candidate scan", "q:v2"))

    for i in range(9, 17):
        lanes.append(LaneAssignment(i, LaneRole.V3_DISCOVERY, "V3/Algebra slot0/liquidity/fee-tier sync", "q:v3"))

    for i in range(17, 21):
        lanes.append(LaneAssignment(i, LaneRole.CURVE_BALANCER_SYNC, "Curve/Balancer state sync", "q:specialized"))

    for i in range(21, 25):
        lanes.append(LaneAssignment(i, LaneRole.AGGREGATOR_ENRICHMENT, "Aggregator quote enrichment only", "q:aggregators"))

    for i in range(25, 29):
        lanes.append(LaneAssignment(i, LaneRole.FORK_SIMULATION, "Fork/static simulation and payload validation", "q:fork"))

    lanes.append(LaneAssignment(29, LaneRole.C2_RECOMPUTE, "Post-C1 C2 recompute lane A", "q:c2"))
    lanes.append(LaneAssignment(30, LaneRole.C2_RECOMPUTE, "Post-C1 C2 recompute lane B", "q:c2"))
    lanes.append(LaneAssignment(31, LaneRole.DNA_REDIS_LOGGING, "DNA card, Redis, universe updates", "q:dna"))
    lanes.append(LaneAssignment(32, LaneRole.HEALTH_FAILSAFE, "Endpoint health and kill-switch", "q:health"))

    return lanes


def route_pool_to_lane(pool_family: str, index_hint: int = 0) -> int:
    family = (pool_family or "").lower()

    if family == "v2_cpmm":
        return 1 + (index_hint % 8)
    if family in {"v3_clmm", "algebra_clmm"}:
        return 9 + (index_hint % 8)
    if family in {"curve_stable", "balancer_weighted", "balancer_stable"}:
        return 17 + (index_hint % 4)
    if family == "aggregator":
        return 21 + (index_hint % 4)

    return 32
