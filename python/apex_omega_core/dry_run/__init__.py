"""
Apex-Omega Dry-Run DNA Dashboard Module

Provides complete dry-run infrastructure with DNA card logging,
block-level cycle tracking, and real-time dashboard streaming.

Exports:
    DNADataSchema: Pydantic models for DNA card data
    DryRunLogger: JSONL logging infrastructure
    BlockCycleIndex: Block-level cycle tracking
    DryRunOrchestrator: Main orchestration logic
    RealtimeBus: Dashboard event streaming
    run_first20_dna_dry_run: CLI entry point
"""

from apex_omega_core.dry_run.dna_schema import (
    DNADataSchema,
    C1AggressorCard,
    C2SurgeonCard,
    CyclePair,
    BlockCycle,
    RouteProfile,
    ReservesState,
    DiscoveryPricing,
    CostStack,
    RouteEnvelope,
    Decision,
    Payload,
    EVProbability,
    Audit,
    Dashboard,
    Replay,
)

from apex_omega_core.dry_run.dna_logger import (
    DryRunLogger,
    get_dry_run_logger,
)

from apex_omega_core.dry_run.block_cycle_index import (
    BlockCycleIndex,
    get_block_cycle_index,
)

from apex_omega_core.dry_run.dry_run_orchestrator import (
    DryRunOrchestrator,
    get_dry_run_orchestrator,
)

from apex_omega_core.dry_run.realtime_bus import (
    RealtimeBus,
    get_realtime_bus,
    DryRunEvent,
)

__all__ = [
    # Schema
    "DNADataSchema",
    "C1AggressorCard",
    "C2SurgeonCard",
    "CyclePair",
    "BlockCycle",
    "RouteProfile",
    "ReservesState",
    "DiscoveryPricing",
    "CostStack",
    "RouteEnvelope",
    "Decision",
    "Payload",
    "EVProbability",
    "Audit",
    "Dashboard",
    "Replay",
    # Logger
    "DryRunLogger",
    "get_dry_run_logger",
    # Block Cycle
    "BlockCycleIndex",
    "get_block_cycle_index",
    # Orchestrator
    "DryRunOrchestrator",
    "get_dry_run_orchestrator",
    # Realtime Bus
    "RealtimeBus",
    "get_realtime_bus",
    "DryRunEvent",
]