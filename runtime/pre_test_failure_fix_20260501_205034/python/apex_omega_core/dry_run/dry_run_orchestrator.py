"""
Dry Run Orchestrator

Main orchestration logic for the dry-run DNA dashboard system.
Coordinates scanner, gates, C1/C2 execution, and logging.

Classes:
    DryRunOrchestrator: Main orchestrator class
    get_dry_run_orchestrator: Singleton accessor
"""

import threading
from datetime import datetime
from typing import Any, Callable, Optional

from apex_omega_core.dry_run.dna_logger import get_dry_run_logger
from apex_omega_core.dry_run.block_cycle_index import get_block_cycle_index
from apex_omega_core.dry_run.realtime_bus import get_realtime_bus, DryRunEvent
from apex_omega_core.safety.dry_run_guard import (
    assert_dry_run_env,
    assert_no_broadcast,
    assert_no_signing,
    is_dry_run_mode,
)


class DryRunOrchestrator:
    """
    Orchestrates the dry-run DNA dashboard flow.
    
    Flow:
        scanner â†’ gates â†’ C1 â†’ fork sim â†’ shadow execution â†’ 
        reload state â†’ C2 â†’ fork sim â†’ shadow execution/NO_OP â†’ log
    """
    
    def __init__(
        self,
        limit: int = 20,
        scanner_fn: Optional[Callable] = None,
        c1_fn: Optional[Callable] = None,
        c2_fn: Optional[Callable] = None,
        fork_sim_fn: Optional[Callable] = None,
        log_dir: Optional[str] = None,
    ):
        """
        Initialize orchestrator.
        
        Args:
            limit: Maximum number of C1 cycles to run.
            scanner_fn: Scanner function to get candidates.
            c1_fn: C1 evaluation function.
            c2_fn: C2 evaluation function.
            fork_sim_fn: Fork simulation function.
        """
        self._limit = limit
        self._scanner_fn = scanner_fn
        self._c1_fn = c1_fn
        self._c2_fn = c2_fn
        self._fork_sim_fn = fork_sim_fn
        
        # Components
        self._logger = get_dry_run_logger(log_dir)
        self._block_index = get_block_cycle_index(log_dir)
        self._realtime_bus = get_realtime_bus(log_dir)
        
        # State
        self._lock = threading.Lock()
        self._running = False
        self._completed_cycles = 0
        self._started_at: Optional[datetime] = None
        self._ended_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
    
    def start(self) -> dict:
        """
        Start the dry-run.
        
        Returns:
            Dictionary with start status.
        """
        with self._lock:
            if self._running:
                return {"status": "already_running"}
            
            # Assert dry-run environment
            assert_dry_run_env()
            
            # Reset state
            self._logger.reset()
            self._block_index.reset()
            self._realtime_bus.clear()
            
            self._running = True
            self._completed_cycles = 0
            self._started_at = datetime.now()
            self._last_error = None
            
            # Log start event
            self._realtime_bus.emit(DryRunEvent.DRY_RUN_STARTED, {
                "limit": self._limit,
                "started_at": self._started_at.isoformat(),
            })
            
            return {"status": "started", "limit": self._limit}

    def run(self, limit: Optional[int] = None) -> dict:
        """Run cycles from the configured live scanner only."""
        if limit is not None:
            self._limit = limit
        if self._scanner_fn is None:
            raise RuntimeError("LIVE_DATA_SCANNER_REQUIRED: dry-run cannot use synthetic/demo candidates")
        self.start()
        for i in range(self._limit):
            scanned = self._scanner_fn()
            candidate = scanned[i] if isinstance(scanned, list) else scanned
            c1_result = self._c1_fn(candidate) if self._c1_fn else None
            c2_result = self._c2_fn(candidate) if self._c2_fn else None
            self.run_cycle(candidate, c1_result, c2_result)
        stats = self.get_stats()
        return {
            "requested_limit": self._limit,
            "completed_cycles": stats.get("cycles_completed", 0),
            "dna_cards": stats.get("total_dna_cards", 0),
            "cycle_pairs": stats.get("cycle_pairs", 0),
        }
    
    def run_cycle(
        self,
        candidate: dict,
        c1_result: Optional[dict] = None,
        c2_result: Optional[dict] = None,
    ) -> dict:
        """
        Run a single C1/C2 cycle.
        
        Args:
            candidate: Candidate data from scanner.
            c1_result: Result from C1 evaluation.
            c2_result: Result from C2 evaluation.
        
        Returns:
            Dictionary with cycle result.
        """
        with self._lock:
            if not self._running:
                return {"status": "not_running"}
            
            if self._completed_cycles >= self._limit:
                return {"status": "limit_reached"}
            
            block_number = candidate.get("block_number", 0)
            
            # Get cycle identifiers
            cycle_ids = self._block_index.next_cycle(block_number)
            
            # Emit candidate scanned event
            self._realtime_bus.emit(DryRunEvent.CANDIDATE_SCANNED, {
                "opportunity_id": cycle_ids["opportunity_id"],
                "block_number": block_number,
            })
            
            # Run C1 evaluation
            if self._c1_fn and c1_result is None:
                c1_result = self._c1_fn(candidate)
            
            # Check C1 result
            if c1_result is None or not c1_result.get("accepted", False):
                # Reject candidate
                rejection_data = {
                    **candidate,
                    **cycle_ids,
                    "rejection_reason": c1_result.get("reason", "C1_REJECTED") if c1_result else "NO_C1_RESULT",
                }
                self._logger.log_rejection(rejection_data)
                self._realtime_bus.emit(DryRunEvent.CANDIDATE_REJECTED, {
                    "opportunity_id": cycle_ids["opportunity_id"],
                    "reason": rejection_data["rejection_reason"],
                })
                return {"status": "rejected", "reason": rejection_data["rejection_reason"]}
            
            # C1 passed - log C1 card
            self._realtime_bus.emit(DryRunEvent.C1_PAYLOAD_BUILT, {
                "opportunity_id": cycle_ids["opportunity_id"],
                "c1_card_id": cycle_ids["c1_card_id"],
            })
            c1_net = c1_result.get("simulated_net_usd", 0) if c1_result else 0
            self._logger.log_c1_card({
                "identity": {
                    "card_id": cycle_ids["c1_card_id"],
                    "cycle_id": cycle_ids["cycle_id"],
                    "global_cycle_number": cycle_ids["global_cycle_number"],
                    "block_cycle_number": cycle_ids["block_cycle_number"],
                    "block_number": block_number,
                    "block_id": cycle_ids["block_id"],
                    "opportunity_id": cycle_ids["opportunity_id"],
                    "strike_role": "C1",
                    "strike_name": "Aggressor",
                    "decision": "BUILD_PAYLOAD",
                },
                "decision": {
                    "payload_built": True,
                    "realized_status": "DRY_RUN_NO_BROADCAST",
                },
                "math": {
                    "net_profit_usd": c1_net,
                },
                "payload": {
                    "would_sign": False,
                    "would_broadcast": False,
                },
            })
            self._logger.log_payload_build({
                "opportunity_id": cycle_ids["opportunity_id"],
                "cycle_id": cycle_ids["cycle_id"],
                "strike_role": "C1",
                "would_sign": False,
                "would_broadcast": False,
            })
            
            # Run fork simulation if available
            fork_sim_pass = True
            if self._fork_sim_fn:
                fork_sim_pass = self._fork_sim_fn(c1_result)
                if fork_sim_pass:
                    self._realtime_bus.emit(DryRunEvent.C1_FORK_SIM_PASS, {
                        "opportunity_id": cycle_ids["opportunity_id"],
                    })
                else:
                    self._realtime_bus.emit(DryRunEvent.C1_FORK_SIM_FAIL, {
                        "opportunity_id": cycle_ids["opportunity_id"],
                    })
            
            # Run C2 evaluation
            if self._c2_fn and c2_result is None:
                c2_result = self._c2_fn(candidate)
            
            # Determine C2 decision
            c2_decision = "NO_OP"
            c2_payload_built = False
            if c2_result:
                action = c2_result.get("action", c2_result.get("decision", "NO_OP"))
                c2_decision = str(action).upper()
                c2_payload_built = c2_decision == "EXECUTE"
            
            # Emit C2 evaluation event
            if c2_decision == "EXECUTE":
                self._realtime_bus.emit(DryRunEvent.C2_PAYLOAD_BUILT, {
                    "opportunity_id": cycle_ids["opportunity_id"],
                    "c2_card_id": cycle_ids["c2_card_id"],
                })
            else:
                self._realtime_bus.emit(DryRunEvent.C2_NO_OP, {
                    "opportunity_id": cycle_ids["opportunity_id"],
                    "c2_card_id": cycle_ids["c2_card_id"],
                })
            
            # Calculate simulated net. Evaluated C2 edge is only executable
            # when the C2 gate chooses EXECUTE; NO_OP contributes zero.
            evaluated_c2_net = c2_result.get("simulated_net_usd", 0) if c2_result else 0
            c2_net = evaluated_c2_net if c2_payload_built else 0
            simulated_net = c1_net + c2_net
            self._logger.log_c2_card({
                "identity": {
                    "card_id": cycle_ids["c2_card_id"],
                    "cycle_id": cycle_ids["cycle_id"],
                    "global_cycle_number": cycle_ids["global_cycle_number"],
                    "block_cycle_number": cycle_ids["block_cycle_number"],
                    "block_number": block_number,
                    "block_id": cycle_ids["block_id"],
                    "opportunity_id": cycle_ids["opportunity_id"],
                    "trigger": cycle_ids["c1_card_id"],
                    "strike_role": "C2",
                    "strike_name": "Surgeon",
                    "decision": c2_decision,
                },
                "decision": {
                    "payload_built": c2_payload_built,
                    "no_op_reason": None if c2_payload_built else "EV<=0_OR_POST_C1_NO_EDGE",
                    "realized_status": "DRY_RUN_NO_BROADCAST",
                },
                "math": {
                    "net_profit_usd": c2_net,
                    "evaluated_net_profit_usd": evaluated_c2_net,
                },
                "payload": {
                    "would_sign": False,
                    "would_broadcast": False,
                },
            })
            
            # Determine cycle status
            cycle_status = (
                "C1_BUILT_C2_EXECUTE" if c2_decision == "EXECUTE"
                else "C1_BUILT_C2_NO_OP"
            )
            
            # Log cycle pair
            from apex_omega_core.dry_run.dna_schema import CyclePair
            cycle_pair = CyclePair(
                block_number=block_number,
                block_id=cycle_ids["block_id"],
                block_cycle_number=cycle_ids["block_cycle_number"],
                global_cycle_number=cycle_ids["global_cycle_number"],
                cycle_id=cycle_ids["cycle_id"],
                opportunity_id=cycle_ids["opportunity_id"],
                c1_card_id=cycle_ids["c1_card_id"],
                c2_card_id=cycle_ids["c2_card_id"],
                c1_decision="BUILD_PAYLOAD",
                c2_decision=c2_decision,
                simulated_c1_net_usd=c1_net,
                simulated_c2_net_usd=c2_net,
                simulated_net_usd=simulated_net,
                realized_net_opportunity_usd=None,
                realized_status="DRY_RUN_NO_BROADCAST",
                cycle_status=cycle_status,
            )
            self._logger.log_cycle_pair(cycle_pair)
            self._realtime_bus.emit(DryRunEvent.CYCLE_PAIR_CREATED, {
                "opportunity_id": cycle_ids["opportunity_id"],
                "cycle_status": cycle_status,
            })
            
            # Update block cycle summary
            self._update_block_summary(block_number)
            
            # Increment cycle count
            self._completed_cycles += 1
            
            # Check if limit reached
            if self._completed_cycles >= self._limit:
                self._end()
            
            return {
                "status": "completed",
                "cycle_ids": cycle_ids,
                "c1_net": c1_net,
                "c2_net": c2_net,
                "simulated_net": simulated_net,
                "cycle_status": cycle_status,
                "cycles_completed": self._completed_cycles,
                "limit": self._limit,
            }
    
    def _update_block_summary(self, block_number: int) -> None:
        """Update block cycle summary."""
        # Get all cycle pairs for this block
        cycle_pairs = self._logger.read_cycle_pairs()
        block_pairs = [p for p in cycle_pairs if p.get("block_number") == block_number]
        
        if not block_pairs:
            return
        
        # Calculate block totals
        global_cycles = [p.get("global_cycle_number") for p in block_pairs]
        opportunity_ids = [p.get("opportunity_id") for p in block_pairs]
        block_net = sum(p.get("simulated_net_usd", 0) or 0 for p in block_pairs)
        
        # Log block cycle
        from apex_omega_core.dry_run.dna_schema import BlockCycle
        block_cycle = BlockCycle(
            block_number=block_number,
            block_id=f"block_{block_number}",
            block_cycle_count=len(block_pairs),
            global_cycle_numbers=global_cycles,
            opportunity_ids=opportunity_ids,
            block_simulated_net_usd=block_net,
            block_realized_net_opportunity_usd=None,
            realized_status="DRY_RUN_NO_BROADCAST",
        )
        self._logger.log_block_cycle(block_cycle)
        
        self._realtime_bus.emit(DryRunEvent.BLOCK_SUMMARY_UPDATED, {
            "block_number": block_number,
            "block_cycle_count": len(block_pairs),
            "block_simulated_net_usd": block_net,
        })
    
    def stop(self) -> dict:
        """
        Stop the dry-run.
        
        Returns:
            Dictionary with stop status.
        """
        with self._lock:
            if not self._running:
                return {"status": "not_running"}
            
            self._end()
            
            return {"status": "stopped", "cycles_completed": self._completed_cycles}
    
    def _end(self) -> None:
        """End the dry-run."""
        self._running = False
        self._ended_at = datetime.now()
        
        # Write summary
        summary = {
            "status": "completed" if self._completed_cycles >= self._limit else "stopped",
            "limit": self._limit,
            "cycles_completed": self._completed_cycles,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "duration_seconds": (
                (self._ended_at - self._started_at).total_seconds()
                if self._started_at and self._ended_at
                else None
            ),
            "last_error": self._last_error,
        }
        
        self._logger.write_summary(summary)
        self._realtime_bus.emit(DryRunEvent.DRY_RUN_DONE, summary)
    
    def get_status(self) -> dict:
        """
        Get current orchestrator status.
        
        Returns:
            Dictionary with status.
        """
        with self._lock:
            return {
                "running": self._running,
                "cycles_completed": self._completed_cycles,
                "limit": self._limit,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "ended_at": self._ended_at.isoformat() if self._ended_at else None,
                "last_error": self._last_error,
            }
    
    def get_stats(self) -> dict:
        """
        Get statistics.
        
        Returns:
            Dictionary with stats.
        """
        return {
            **self._logger.get_stats(),
            **self.get_status(),
        }


# Singleton instance
_orchestrator: Optional[DryRunOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_dry_run_orchestrator(
    limit: int = 20,
    scanner_fn: Optional[Callable] = None,
    c1_fn: Optional[Callable] = None,
    c2_fn: Optional[Callable] = None,
    fork_sim_fn: Optional[Callable] = None,
    log_dir: Optional[str] = None,
) -> DryRunOrchestrator:
    """
    Get singleton DryRunOrchestrator instance.
    
    Args:
        limit: Maximum number of C1 cycles.
        scanner_fn: Scanner function.
        c1_fn: C1 evaluation function.
        c2_fn: C2 evaluation function.
        fork_sim_fn: Fork simulation function.
    
    Returns:
        DryRunOrchestrator singleton instance.
    """
    global _orchestrator
    
    with _orchestrator_lock:
        if _orchestrator is None:
            _orchestrator = DryRunOrchestrator(
                limit=limit,
                scanner_fn=scanner_fn,
                c1_fn=c1_fn,
                c2_fn=c2_fn,
                fork_sim_fn=fork_sim_fn,
                log_dir=log_dir,
            )
        return _orchestrator


def reset_dry_run_orchestrator() -> None:
    """Reset the singleton orchestrator."""
    global _orchestrator
    
    with _orchestrator_lock:
        if _orchestrator:
            _orchestrator.stop()
        _orchestrator = None
