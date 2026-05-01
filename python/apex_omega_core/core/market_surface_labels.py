"""Compatibility shim for canonical flash size ladder labels."""

from typing import Literal

from apex_omega_core.ladder.zone import (
    CONDITIONAL,
    EXECUTABLE,
    PROBE_ONLY,
    classify_flash_ladder_zone,
    is_size_zone_allowed_for_c1,
)

LadderZone = Literal["EXECUTABLE", "CONDITIONAL", "PROBE_ONLY"]

__all__ = [
    "CONDITIONAL",
    "EXECUTABLE",
    "LadderZone",
    "PROBE_ONLY",
    "classify_flash_ladder_zone",
    "is_size_zone_allowed_for_c1",
]
