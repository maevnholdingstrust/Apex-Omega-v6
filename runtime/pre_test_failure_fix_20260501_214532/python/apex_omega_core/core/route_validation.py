# apex_omega_core/core/route_validation.py

from typing import List
from dataclasses import dataclass

from .venue_registry import (
    VenueDNA,
    assert_known_venue,
    PoolFamily
)


def validate_route_step_target(target: str) -> VenueDNA:
    """
    HARD GATE:
    - Reject unknown venues
    - Reject unsupported pool families
    """

    venue_dna = assert_known_venue(target)

    if venue_dna.family == PoolFamily.UNKNOWN:
        raise ValueError(
            f"UNSUPPORTED_POOL_FAMILY: {venue_dna.venue_id} | {venue_dna.address}"
        )

    return venue_dna