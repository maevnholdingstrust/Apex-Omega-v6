"""Canonical contract target addresses and role constants for strategy execution.

Canonical role definitions (spec-locked)
-----------------------------------------
C1 = Aggressor
    Executes Punch 1 first.  Consumes the initial edge.  Is unaware of C2.
    Mutates on-chain state.

C2 = Surgeon
    Observes what C1 did.  Re-evaluates the post-C1 state across blocks
    N+1 through N+5.  Decides whether a second executable profit exists.
    May mirror, counter, modify, or do nothing.  If no executable EV
    remains, logs null and does not fire.

Shortest precise form
    C1 = first punch.
    C2 = post-impact second-strike decision engine for N+1 to N+5.
"""

C1_TARGET = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
C2_TARGET = "0x0466759822ABAA7E416276E1cf2b538d7FC540BD"

# Maximum number of blocks after C1 execution in which C2 may still fire.
# C2 re-evaluates the post-C1 state for blocks N+1 through N+5 inclusive
# (5 blocks total).  C2_BLOCK_WINDOW = 5 is the count of eligible blocks.
C2_BLOCK_WINDOW: int = 5