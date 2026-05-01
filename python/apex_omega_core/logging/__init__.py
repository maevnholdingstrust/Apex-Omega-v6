"""
DNA Card Cycle and Block-Level Logging System

This module provides comprehensive tracking and logging of each executable opportunity
through the C1 (Aggressor) and C2 (Surgeon) execution flow.

Exports:
- DNACard: Data class for individual DNA cards
- CyclePair: Data class for paired C1/C2 execution
- BlockCycle: Data class for block-level aggregates
- DNALoggingSystem: Thread-safe logging infrastructure
- get_dna_logging_system: Singleton accessor
- reset_dna_logging_system: Singleton reset
"""

from apex_omega_core.logging.dna_card_logger import (
    DNACard,
    CyclePair,
    BlockCycle,
    DNALoggingSystem,
    get_dna_logging_system,
    reset_dna_logging_system,
)

__all__ = [
    "DNACard",
    "CyclePair", 
    "BlockCycle",
    "DNALoggingSystem",
    "get_dna_logging_system",
    "reset_dna_logging_system",
]