"""
Realtime Bus - Dashboard Event Streaming

Provides real-time event streaming for dashboard updates.
Supports SSE and event-based streaming.

Classes:
    DryRunEvent: Enum of dashboard events
    RealtimeBus: Event streaming bus
    get_realtime_bus: Singleton accessor
"""

import json
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


class DryRunEvent(str, Enum):
    """Dashboard event types."""
    
    DRY_RUN_STARTED = "DRY_RUN_STARTED"
    CANDIDATE_SCANNED = "CANDIDATE_SCANNED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    C1_PAYLOAD_BUILT = "C1_PAYLOAD_BUILT"
    C1_FORK_SIM_PASS = "C1_FORK_SIM_PASS"
    C1_FORK_SIM_FAIL = "C1_FORK_SIM_FAIL"
    C1_SHADOW_STATE_APPLIED = "C1_SHADOW_STATE_APPLIED"
    C2_EVALUATION_STARTED = "C2_EVALUATION_STARTED"
    C2_PAYLOAD_BUILT = "C2_PAYLOAD_BUILT"
    C2_NO_OP = "C2_NO_OP"
    C2_FORK_SIM_PASS = "C2_FORK_SIM_PASS"
    C2_FORK_SIM_FAIL = "C2_FORK_SIM_FAIL"
    DNA_CARD_CREATED = "DNA_CARD_CREATED"
    CYCLE_PAIR_CREATED = "CYCLE_PAIR_CREATED"
    BLOCK_SUMMARY_UPDATED = "BLOCK_SUMMARY_UPDATED"
    DRY_RUN_DONE = "DRY_RUN_DONE"
    DRY_RUN_ABORTED = "DRY_RUN_ABORTED"
    BROADCAST_ATTEMPT_BLOCKED = "BROADCAST_ATTEMPT_BLOCKED"


class RealtimeBus:
    """
    Real-time event bus for dashboard streaming.
    
    Provides:
    - Event emission to subscribers
    - JSONL logging of all events
    - SSE endpoint support
    """
    
    def __init__(self, log_dir: Optional[str] = None):
        """
        Initialize realtime bus.
        
        Args:
            log_dir: Directory for event log. Defaults to logs/.
        """
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            repo_root = Path(__file__).parent.parent.parent.parent
            self.log_dir = repo_root / "logs"
        
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Event log path
        self.events_path = self.log_dir / "dry_run_dashboard_events.jsonl"
        
        # Subscribers
        self._subscribers: list[Callable[[DryRunEvent, dict], None]] = []
        self._subscriber_lock = threading.Lock()
        
        # Event history
        self._events: list[dict] = []
        self._max_history = 1000
        self._events_lock = threading.Lock()
        
        # Initialize log file
        if not self.events_path.exists():
            self.events_path.write_text("", encoding="utf-8")
    
    def subscribe(self, callback: Callable[[DryRunEvent, dict], None]) -> None:
        """
        Subscribe to events.
        
        Args:
            callback: Function to call on each event.
        """
        with self._subscriber_lock:
            self._subscribers.append(callback)
    
    def unsubscribe(self, callback: Callable[[DryRunEvent, dict], None]) -> None:
        """
        Unsubscribe from events.
        
        Args:
            callback: Callback to remove.
        """
        with self._subscriber_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
    
    def emit(self, event: DryRunEvent, data: dict) -> None:
        """
        Emit an event.
        
        Args:
            event: Event type.
            data: Event data.
        """
        timestamp = datetime.now().isoformat()
        
        event_record = {
            "event": event.value,
            "timestamp": timestamp,
            **data,
        }
        
        # Log to file
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_record, ensure_ascii=False) + "\n")
        
        # Store in history
        with self._events_lock:
            self._events.append(event_record)
            if len(self._events) > self._max_history:
                self._events = self._events[-self._max_history:]
        
        # Notify subscribers
        with self._subscriber_lock:
            for callback in self._subscribers:
                try:
                    callback(event, data)
                except Exception:
                    # Log but don't fail
                    pass
    
    def get_recent_events(self, limit: int = 100) -> list[dict]:
        """
        Get recent events.
        
        Args:
            limit: Maximum number of events to return.
        
        Returns:
            List of event records.
        """
        with self._events_lock:
            return self._events[-limit:]
    
    def get_events_by_type(self, event_type: DryRunEvent) -> list[dict]:
        """
        Get events of a specific type.
        
        Args:
            event_type: Type of events to get.
        
        Returns:
            List of event records.
        """
        with self._events_lock:
            return [e for e in self._events if e.get("event") == event_type.value]
    
    def clear(self) -> None:
        """Clear event history."""
        with self._events_lock:
            self._events.clear()
        
        if self.events_path.exists():
            self.events_path.write_text("", encoding="utf-8")
    
    def get_sse_format(self, event: DryRunEvent, data: dict) -> str:
        """
        Format event for SSE streaming.
        
        Args:
            event: Event type.
            data: Event data.
        
        Returns:
            SSE-formatted string.
        """
        return f"event: {event.value}\ndata: {json.dumps(data)}\n\n"
    
    def read_events_from_log(self, limit: Optional[int] = None) -> list[dict]:
        """
        Read events from log file.
        
        Args:
            limit: Maximum number of events to read.
        
        Returns:
            List of event records.
        """
        if not self.events_path.exists():
            return []
        
        events = []
        with open(self.events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        
        if limit:
            return events[-limit:]
        return events


# Singleton instance
_realtime_bus: Optional[RealtimeBus] = None
_bus_lock = threading.Lock()


def get_realtime_bus(log_dir: Optional[str] = None) -> RealtimeBus:
    """
    Get singleton RealtimeBus instance.
    
    Args:
        log_dir: Optional log directory override.
    
    Returns:
        RealtimeBus singleton instance.
    """
    global _realtime_bus
    
    with _bus_lock:
        requested_log_dir = Path(log_dir).resolve() if log_dir else None
        current_log_dir = (
            _realtime_bus.log_dir.resolve() if _realtime_bus is not None else None
        )
        if _realtime_bus is None or (
            requested_log_dir is not None and requested_log_dir != current_log_dir
        ):
            _realtime_bus = RealtimeBus(log_dir)
        return _realtime_bus


def reset_realtime_bus() -> None:
    """Reset the singleton bus."""
    global _realtime_bus
    
    with _bus_lock:
        if _realtime_bus:
            _realtime_bus.clear()
        _realtime_bus = None
