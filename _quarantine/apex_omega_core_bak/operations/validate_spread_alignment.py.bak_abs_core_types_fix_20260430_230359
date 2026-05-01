from apex_omega_core.core.spread_alignment import align_spread
from apex_omega_core.core.types import Spread

def validate_spread_alignment(spread: Spread) -> bool:
    """Alignment verification."""
    aligned = align_spread(spread)
    # Validation logic
    return aligned.bid < aligned.ask