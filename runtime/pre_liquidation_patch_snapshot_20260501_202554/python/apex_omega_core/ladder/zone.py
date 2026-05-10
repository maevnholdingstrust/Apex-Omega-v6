from __future__ import annotations

EXECUTABLE = "EXECUTABLE"
CONDITIONAL = "CONDITIONAL"
PROBE_ONLY = "PROBE_ONLY"

MAX_EXECUTABLE_FRACTION = 0.05
MAX_CONDITIONAL_FRACTION = 0.10


def classify_flash_ladder_zone(fraction_of_step1_tvl: float) -> str:
    if fraction_of_step1_tvl <= 0:
        return PROBE_ONLY

    if fraction_of_step1_tvl <= MAX_EXECUTABLE_FRACTION:
        return EXECUTABLE

    if fraction_of_step1_tvl <= MAX_CONDITIONAL_FRACTION:
        return CONDITIONAL

    return PROBE_ONLY


def is_size_zone_allowed_for_c1(zone: str) -> bool:
    return zone in {EXECUTABLE, CONDITIONAL}
