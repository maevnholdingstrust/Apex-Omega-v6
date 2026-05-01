"""
DNA Logger - JSONL Logging Infrastructure

Provides thread-safe JSONL logging for all dry-run DNA cards,
cycle pairs, block cycles, and dashboard events.

Classes:
    DryRunLogger: Main logging class
    get_dry_run_logger: Singleton accessor
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apex_omega_core.dry_run.dna_schema import (
    C1AggressorCard,
    C2SurgeonCard,
    CyclePair,
    BlockCycle,
)


class DryRunLogger:
    """
    Thread-safe JSONL logger for dry-run DNA cards.
    
    Writes to:
    - logs/dry_run_dna_cards.jsonl
    - logs/dry_run_cycle_pairs.jsonl
    - logs/dry_run_block_cycles.jsonl
    - logs/dry_run_payload_builds.jsonl
    - logs/dry_run_rejections.jsonl
    - logs/dry_run_dashboard_events.jsonl
    - logs/dry_run_summary.json
    """
    
    def __init__(self, log_dir: Optional[str] = None):
        """
        Initialize logger.
        
        Args:
            log_dir: Directory for log files. Defaults to logs/ in repo root.
        """
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            # Default to repo root logs/
            repo_root = Path(__file__).parent.parent.parent.parent
            self.log_dir = repo_root / "logs"
        
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.dna_cards_path = self.log_dir / "dry_run_dna_cards.jsonl"
        self.cycle_pairs_path = self.log_dir / "dry_run_cycle_pairs.jsonl"
        self.block_cycles_path = self.log_dir / "dry_run_block_cycles.jsonl"
        self.payload_builds_path = self.log_dir / "dry_run_payload_builds.jsonl"
        self.rejections_path = self.log_dir / "dry_run_rejections.jsonl"
        self.dashboard_events_path = self.log_dir / "dry_run_dashboard_events.jsonl"
        self.summary_path = self.log_dir / "dry_run_summary.json"
        
        # Thread lock
        self._lock = threading.Lock()
        
        # Counters
        self._c1_count = 0
        self._c2_count = 0
        self._cycle_pair_count = 0
        self._block_cycle_count = 0
        self._rejection_count = 0
        
        # Initialize files
        self._init_files()
    
    def _init_files(self) -> None:
        """Initialize log files with empty arrays."""
        for path in [
            self.dna_cards_path,
            self.cycle_pairs_path,
            self.block_cycles_path,
            self.payload_builds_path,
            self.rejections_path,
            self.dashboard_events_path,
        ]:
            if not path.exists():
                path.write_text("", encoding="utf-8")
    
    def _write_jsonl(self, path: Path, record: dict) -> None:
        """
        Write a single JSONL record.
        
        Args:
            path: File path to write to.
            record: Dictionary to write as JSON line.
        """
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def log_c1_card(self, card: C1AggressorCard) -> None:
        """
        Log a C1 Aggressor card.
        
        Args:
            card: C1AggressorCard instance.
        """
        record = card.model_dump()
        record["schema_type"] = "c1_card"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.dna_cards_path, record)
        self._c1_count += 1
    
    def log_c2_card(self, card: C2SurgeonCard) -> None:
        """
        Log a C2 Surgeon card.
        
        Args:
            card: C2SurgeonCard instance.
        """
        record = card.model_dump()
        record["schema_type"] = "c2_card"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.dna_cards_path, record)
        self._c2_count += 1
    
    def log_cycle_pair(self, pair: CyclePair) -> None:
        """
        Log a paired C1/C2 cycle.
        
        Args:
            pair: CyclePair instance.
        """
        record = pair.model_dump()
        record["schema_type"] = "cycle_pair"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.cycle_pairs_path, record)
        self._cycle_pair_count += 1
    
    def log_block_cycle(self, block: BlockCycle) -> None:
        """
        Log a block-level aggregate.
        
        Args:
            block: BlockCycle instance.
        """
        record = block.model_dump()
        record["schema_type"] = "block_cycle"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.block_cycles_path, record)
        self._block_cycle_count += 1
    
    def log_payload_build(self, payload_data: dict) -> None:
        """
        Log a payload build event.
        
        Args:
            payload_data: Dictionary with payload details.
        """
        record = payload_data.copy()
        record["schema_type"] = "payload_build"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.payload_builds_path, record)
    
    def log_rejection(self, rejection_data: dict) -> None:
        """
        Log a rejected candidate.
        
        Args:
            rejection_data: Dictionary with rejection details.
        """
        record = rejection_data.copy()
        record["schema_type"] = "rejection"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.rejections_path, record)
        self._rejection_count += 1
    
    def log_dashboard_event(self, event_data: dict) -> None:
        """
        Log a dashboard event.
        
        Args:
            event_data: Dictionary with event details.
        """
        record = event_data.copy()
        record["schema_type"] = "dashboard_event"
        record["logged_at"] = datetime.now().isoformat()
        
        self._write_jsonl(self.dashboard_events_path, record)
    
    def write_summary(self, summary_data: dict) -> None:
        """
        Write final run summary.
        
        Args:
            summary_data: Dictionary with summary details.
        """
        record = summary_data.copy()
        record["logged_at"] = datetime.now().isoformat()
        
        with self._lock:
            with open(self.summary_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
    
    def get_stats(self) -> dict:
        """
        Get logging statistics.
        
        Returns:
            Dictionary with counts.
        """
        return {
            "c1_cards": self._c1_count,
            "c2_cards": self._c2_count,
            "total_dna_cards": self._c1_count + self._c2_count,
            "cycle_pairs": self._cycle_pair_count,
            "block_cycles": self._block_cycle_count,
            "rejections": self._rejection_count,
        }
    
    def read_dna_cards(self) -> list[dict]:
        """Read all DNA cards from log."""
        return self._read_jsonl(self.dna_cards_path)
    
    def read_cycle_pairs(self) -> list[dict]:
        """Read all cycle pairs from log."""
        return self._read_jsonl(self.cycle_pairs_path)
    
    def read_block_cycles(self) -> list[dict]:
        """Read all block cycles from log."""
        return self._read_jsonl(self.block_cycles_path)
    
    def read_rejections(self) -> list[dict]:
        """Read all rejections from log."""
        return self._read_jsonl(self.rejections_path)
    
    def read_summary(self) -> Optional[dict]:
        """Read summary from log."""
        if self.summary_path.exists():
            with open(self.summary_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    
    def _read_jsonl(self, path: Path) -> list[dict]:
        """Read all records from a JSONL file."""
        if not path.exists():
            return []
        
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records
    
    def reset(self) -> None:
        """Reset logger state and clear log files."""
        with self._lock:
            for path in [
                self.dna_cards_path,
                self.cycle_pairs_path,
                self.block_cycles_path,
                self.payload_builds_path,
                self.rejections_path,
                self.dashboard_events_path,
                self.summary_path,
            ]:
                if path.exists():
                    path.unlink()
            
            self._init_files()
            
            self._c1_count = 0
            self._c2_count = 0
            self._cycle_pair_count = 0
            self._block_cycle_count = 0
            self._rejection_count = 0


# Singleton instance
_dry_run_logger: Optional[DryRunLogger] = None
_logger_lock = threading.Lock()


def get_dry_run_logger(log_dir: Optional[str] = None) -> DryRunLogger:
    """
    Get singleton DryRunLogger instance.
    
    Args:
        log_dir: Optional log directory override.
    
    Returns:
        DryRunLogger singleton instance.
    """
    global _dry_run_logger
    
    with _logger_lock:
        if _dry_run_logger is None:
            _dry_run_logger = DryRunLogger(log_dir)
        return _dry_run_logger


def reset_dry_run_logger() -> None:
    """Reset the singleton logger."""
    global _dry_run_logger
    
    with _logger_lock:
        if _dry_run_logger:
            _dry_run_logger.reset()
        _dry_run_logger = None