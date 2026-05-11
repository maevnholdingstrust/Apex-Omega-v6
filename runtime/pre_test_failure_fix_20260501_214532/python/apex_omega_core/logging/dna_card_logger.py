"""
DNA Card Cycle and Block-Level Logging System

This module provides comprehensive tracking and logging of each executable opportunity
through the C1 (Aggressor) and C2 (Surgeon) execution flow.

Architecture:
- Layer 1: Block Number (Identifier for the blockchain state)
- Layer 2: Block Cycle Index (Sequential identifier within a block)
- Layer 3: Global Cycle Number (Monotonic counter across the entire system)
- Layer 4: Opportunity ID (Unique identifier for each trade opportunity)
- Layer 5: DNA Cards (Paired records representing C1 and C2 actions)

Identifier Formats:
| Entity | Format | Example |
| :--- | :--- | :--- |
| Block ID | block_{number} | block_73491288 |
| Cycle ID | block_{num}_cycle_{b_idx}_global_{g_idx} | block_73491288_cycle_000001_global_000128 |
| Opportunity ID | opportunity_{global_idx} | opportunity_000128 |
| C1 Card ID | opportunity_{global_idx}_c1 | opportunity_000128_c1 |
| C2 Card ID | opportunity_{global_idx}_c2 | opportunity_000128_c2 |
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class DNACard:
    """
    Represents a single DNA card (C1 Aggressor or C2 Surgeon action).
    
    Attributes:
        strike_role: C1 or C2
        strike_name: Aggressor or Surgeon
        sequence_index: 1 for C1, 2 for C2
        trigger: What triggered this card
        state_basis: pre_c1_state or post_c1_reloaded_state
        decision: BUILD_PAYLOAD / REJECT / EXECUTE / NO_OP
        shadow_execution_status: APPLIED_TO_SHADOW_STATE / NOT_APPLIED
        no_op_reason: Required if decision is NO_OP
        c2_never_pre_approved_c1: true (for C2 cards)
    """
    # Identifiers
    card_id: str
    opportunity_id: str
    cycle_id: str
    block_id: str
    block_number: int
    block_cycle_index: int
    global_cycle_number: int
    
    # Role & Sequence
    strike_role: str  # "C1" or "C2"
    strike_name: str  # "Aggressor" or "Surgeon"
    sequence_index: int  # 1 for C1, 2 for C2
    
    # Trigger & State
    trigger: str
    state_basis: str  # "pre_c1_state" or "post_c1_reloaded_state"
    
    # Decision
    decision: str  # "BUILD_PAYLOAD" / "REJECT" / "EXECUTE" / "NO_OP"
    shadow_execution_status: str = "NOT_APPLIED"
    no_op_reason: Optional[str] = None
    
    # C2 specific
    c2_never_pre_approved_c1: bool = False
    
    # Opportunity Details
    pair: str = ""
    buy_dex: str = ""
    sell_dex: str = ""
    trade_size_usd: float = 0.0
    
    # Profit Tracking
    simulated_net_usd: float = 0.0  # Sum of C1 + C2
    realized_net_opportunity_usd: Optional[float] = None  # null for dry runs
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    block_timestamp: Optional[str] = None


@dataclass
class CyclePair:
    """
    Represents the paired execution of C1 and C2 cards within a cycle.
    """
    cycle_id: str
    block_id: str
    block_number: int
    block_cycle_index: int
    global_cycle_number: int
    opportunity_id: str
    
    # C1 Card Reference
    c1_card_id: str
    c1_decision: str
    
    # C2 Card Reference
    c2_card_id: str
    c2_decision: str
    c2_no_op_reason: Optional[str] = None
    
    # Aggregate Profit
    simulated_net_usd: float = 0.0
    realized_net_opportunity_usd: Optional[float] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class BlockCycle:
    """
    Provides an aggregate summary of cycles and total simulated profit for each block.
    """
    block_id: str
    block_number: int
    total_cycles: int = 0
    total_simulated_profit_usd: float = 0.0
    total_realized_profit_usd: Optional[float] = None
    cycles: List[str] = field(default_factory=list)  # List of cycle_ids
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# Logging Infrastructure
# =============================================================================

class DNALoggingSystem:
    """
    Thread-safe logging system for DNA cards, cycle pairs, and block cycles.
    
    Three primary JSONL files:
    1. logs/dry_run_dna_cards.jsonl - Individual C1 and C2 card records
    2. logs/dry_run_cycle_pairs.jsonl - Paired execution summaries
    3. logs/dry_run_block_cycles.jsonl - Block-level aggregates
    """
    
    def __init__(self, logs_dir: str = "logs", dry_run: bool = True):
        self.logs_dir = Path(logs_dir)
        self.dry_run = dry_run
        
        # File paths
        self.dna_cards_file = self.logs_dir / "dry_run_dna_cards.jsonl"
        self.cycle_pairs_file = self.logs_dir / "dry_run_cycle_pairs.jsonl"
        self.block_cycles_file = self.logs_dir / "dry_run_block_cycles.jsonl"
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Counters
        self._global_cycle_number: int = 0
        self._block_cycle_counters: Dict[int, int] = {}  # block_number -> cycle_index
        
        # Ensure logs directory exists
        self.logs_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_next_global_cycle_number(self) -> int:
        """Get the next monotonic global cycle number."""
        with self._lock:
            self._global_cycle_number += 1
            return self._global_cycle_number
    
    def _get_next_block_cycle_index(self, block_number: int) -> int:
        """Get the next cycle index within a block."""
        with self._lock:
            if block_number not in self._block_cycle_counters:
                self._block_cycle_counters[block_number] = 0
            self._block_cycle_counters[block_number] += 1
            return self._block_cycle_counters[block_number]
    
    def _format_global_index(self, idx: int) -> str:
        """Format global index with leading zeros."""
        return f"{idx:06d}"
    
    def _format_block_cycle_index(self, idx: int) -> str:
        """Format block cycle index with leading zeros."""
        return f"{idx:06d}"
    
    def _create_block_id(self, block_number: int) -> str:
        """Create block identifier."""
        return f"block_{block_number}"
    
    def _create_cycle_id(self, block_number: int, block_cycle_index: int, global_cycle_number: int) -> str:
        """Create cycle identifier."""
        return f"block_{block_number}_cycle_{self._format_block_cycle_index(block_cycle_index)}_global_{self._format_global_index(global_cycle_number)}"
    
    def _create_opportunity_id(self, global_cycle_number: int) -> str:
        """Create opportunity identifier."""
        return f"opportunity_{self._format_global_index(global_cycle_number)}"
    
    def _create_card_id(self, opportunity_id: str, role: str) -> str:
        """Create card identifier."""
        return f"{opportunity_id}_{role.lower()}"
    
    def _append_to_jsonl(self, filepath: Path, record: Dict[str, Any]) -> None:
        """Append a record to a JSONL file."""
        with self._lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    
    def log_c1_card(
        self,
        block_number: int,
        candidate: Any,
        c1_result: Any,
        decision: str = "BUILD_PAYLOAD",
        shadow_status: str = "APPLIED_TO_SHADOW_STATE",
    ) -> DNACard:
        """
        Log a C1 (Aggressor) DNA card.
        
        Called when C1 reaches executable payload-build status.
        
        Args:
            block_number: Current blockchain block number
            candidate: The arbitrage candidate
            c1_result: Result from C1 processing
            decision: BUILD_PAYLOAD or REJECT
            shadow_status: APPLIED_TO_SHADOW_STATE or NOT_APPLIED
            
        Returns:
            DNACard: The created C1 card
        """
        global_idx = self._get_next_global_cycle_number()
        block_cycle_idx = self._get_next_block_cycle_index(block_number)
        
        # Create identifiers
        block_id = self._create_block_id(block_number)
        cycle_id = self._create_cycle_id(block_number, block_cycle_idx, global_idx)
        opportunity_id = self._create_opportunity_id(global_idx)
        card_id = self._create_card_id(opportunity_id, "C1")
        
        # Extract candidate details
        pair = getattr(candidate, "pair", "") if hasattr(candidate, "pair") else ""
        buy_dex = getattr(candidate, "buy_dex", "") if hasattr(candidate, "buy_dex") else ""
        sell_dex = getattr(candidate, "sell_dex", "") if hasattr(candidate, "sell_dex") else ""
        trade_size = getattr(candidate, "trade_size_usd", 0.0) if hasattr(candidate, "trade_size_usd") else 0.0
        
        # Create card
        card = DNACard(
            card_id=card_id,
            opportunity_id=opportunity_id,
            cycle_id=cycle_id,
            block_id=block_id,
            block_number=block_number,
            block_cycle_index=block_cycle_idx,
            global_cycle_number=global_idx,
            strike_role="C1",
            strike_name="Aggressor",
            sequence_index=1,
            trigger="scanner_executable_candidate",
            state_basis="pre_c1_state",
            decision=decision,
            shadow_execution_status=shadow_status,
            pair=pair,
            buy_dex=buy_dex,
            sell_dex=sell_dex,
            trade_size_usd=trade_size,
            simulated_net_usd=0.0,  # Will be updated when C2 is logged
            realized_net_opportunity_usd=None if self.dry_run else None,
        )
        
        # Write to file
        self._append_to_jsonl(self.dna_cards_file, asdict(card))
        
        return card
    
    def log_c2_card(
        self,
        c1_card: DNACard,
        c2_result: Any,
        decision: str = "EXECUTE",
        no_op_reason: Optional[str] = None,
        simulated_profit: float = 0.0,
    ) -> DNACard:
        """
        Log a C2 (Surgeon) DNA card.
        
        Must be called after C1 shadow execution and state reload.
        Even if decision is NO_OP, the card must be logged.
        
        Args:
            c1_card: The previously logged C1 card
            c2_result: Result from C2 processing
            decision: EXECUTE or NO_OP
            no_op_reason: Required if decision is NO_OP
            simulated_profit: Simulated net profit from this cycle
            
        Returns:
            DNACard: The created C2 card
        """
        # Create card ID
        card_id = self._create_card_id(c1_card.opportunity_id, "C2")
        
        # Create card
        card = DNACard(
            card_id=card_id,
            opportunity_id=c1_card.opportunity_id,
            cycle_id=c1_card.cycle_id,
            block_id=c1_card.block_id,
            block_number=c1_card.block_number,
            block_cycle_index=c1_card.block_cycle_index,
            global_cycle_number=c1_card.global_cycle_number,
            strike_role="C2",
            strike_name="Surgeon",
            sequence_index=2,
            trigger=c1_card.card_id,
            state_basis="post_c1_reloaded_state",
            decision=decision,
            shadow_execution_status="NOT_APPLIED",
            no_op_reason=no_op_reason,
            c2_never_pre_approved_c1=True,
            pair=c1_card.pair,
            buy_dex=c1_card.buy_dex,
            sell_dex=c1_card.sell_dex,
            trade_size_usd=c1_card.trade_size_usd,
            simulated_net_usd=simulated_profit,
            realized_net_opportunity_usd=None if self.dry_run else None,
        )
        
        # Write to file
        self._append_to_jsonl(self.dna_cards_file, asdict(card))
        
        # Update C1 card with simulated profit
        self._update_c1_profit(c1_card.card_id, simulated_profit)
        
        # Log cycle pair
        self._log_cycle_pair(c1_card, card, simulated_profit)
        
        # Update block cycle
        self._update_block_cycle(c1_card.block_number, simulated_profit)
        
        return card
    
    def _update_c1_profit(self, c1_card_id: str, simulated_profit: float) -> None:
        """Update the C1 card's simulated profit after C2 completes."""
        # Read all records, find and update C1, rewrite
        records = []
        updated = False
        
        try:
            with open(self.dna_cards_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        if record.get("card_id") == c1_card_id:
                            record["simulated_net_usd"] = simulated_profit
                            updated = True
                        records.append(record)
            
            if updated:
                with open(self.dna_cards_file, "w", encoding="utf-8") as f:
                    for record in records:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except FileNotFoundError:
            pass
    
    def _log_cycle_pair(self, c1_card: DNACard, c2_card: DNACard, simulated_profit: float) -> None:
        """Log the paired execution of C1 and C2 cards."""
        pair = CyclePair(
            cycle_id=c1_card.cycle_id,
            block_id=c1_card.block_id,
            block_number=c1_card.block_number,
            block_cycle_index=c1_card.block_cycle_index,
            global_cycle_number=c1_card.global_cycle_number,
            opportunity_id=c1_card.opportunity_id,
            c1_card_id=c1_card.card_id,
            c1_decision=c1_card.decision,
            c2_card_id=c2_card.card_id,
            c2_decision=c2_card.decision,
            c2_no_op_reason=c2_card.no_op_reason,
            simulated_net_usd=simulated_profit,
            realized_net_opportunity_usd=None if self.dry_run else None,
        )
        
        self._append_to_jsonl(self.cycle_pairs_file, asdict(pair))
    
    def _update_block_cycle(self, block_number: int, profit: float) -> None:
        """Update or create block cycle summary."""
        block_id = self._create_block_id(block_number)
        
        # Try to find existing record
        existing_record = None
        records = []
        
        try:
            with open(self.block_cycles_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        if record.get("block_number") == block_number:
                            existing_record = record
                        else:
                            records.append(record)
        except FileNotFoundError:
            pass
        
        if existing_record:
            # Update existing
            existing_record["total_cycles"] = existing_record.get("total_cycles", 0) + 1
            existing_record["total_simulated_profit_usd"] = existing_record.get("total_simulated_profit_usd", 0.0) + profit
            existing_record["updated_at"] = datetime.utcnow().isoformat()
            records.append(existing_record)
        else:
            # Create new
            new_record = {
                "block_id": block_id,
                "block_number": block_number,
                "total_cycles": 1,
                "total_simulated_profit_usd": profit,
                "total_realized_profit_usd": None,
                "cycles": [],
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
            records.append(new_record)
        
        # Rewrite file
        with open(self.block_cycles_file, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get logging system statistics."""
        stats = {
            "global_cycle_number": self._global_cycle_number,
            "blocks_tracked": len(self._block_cycle_counters),
            "dry_run": self.dry_run,
        }
        
        # Count records in each file
        for name, filepath in [
            ("dna_cards", self.dna_cards_file),
            ("cycle_pairs", self.cycle_pairs_file),
            ("block_cycles", self.block_cycles_file),
        ]:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    stats[f"{name}_count"] = sum(1 for _ in f)
            except FileNotFoundError:
                stats[f"{name}_count"] = 0
        
        return stats
    
    def reset(self) -> None:
        """Reset all counters and clear log files. Use with caution."""
        with self._lock:
            self._global_cycle_number = 0
            self._block_cycle_counters.clear()
            
            for filepath in [self.dna_cards_file, self.cycle_pairs_file, self.block_cycles_file]:
                if filepath.exists():
                    filepath.unlink()


# =============================================================================
# Singleton Instance
# =============================================================================

_logging_system: Optional[DNALoggingSystem] = None
_logging_system_lock = threading.Lock()


def get_dna_logging_system(logs_dir: str = "logs", dry_run: bool = True) -> DNALoggingSystem:
    """
    Get the singleton DNA logging system instance.
    
    Args:
        logs_dir: Directory for log files
        dry_run: Whether this is a dry run (affects realized profit recording)
        
    Returns:
        DNALoggingSystem: The logging system instance
    """
    global _logging_system
    
    if _logging_system is None:
        with _logging_system_lock:
            if _logging_system is None:
                _logging_system = DNALoggingSystem(logs_dir, dry_run)
    
    return _logging_system


def reset_dna_logging_system() -> None:
    """Reset the singleton logging system."""
    global _logging_system
    
    with _logging_system_lock:
        if _logging_system is not None:
            _logging_system.reset()
        _logging_system = None