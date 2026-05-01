from __future__ import annotations

from typing import Literal

LadderZone = Literal["EXECUTABLE", "CONDITIONAL", "PROBE_ONLY"]


def classify_flash_ladder_zone(fraction_of_leg1_tvl: float) -> LadderZone:
    if fraction_of_leg1_tvl <= 0.05:
        return "EXECUTABLE"
    if fraction_of_leg1_tvl <= 0.10:
        return "CONDITIONAL"
    return "PROBE_ONLY"


def is_size_zone_allowed_for_c1(zone: str) -> bool:
    return zone in {"EXECUTABLE", "CONDITIONAL"}
