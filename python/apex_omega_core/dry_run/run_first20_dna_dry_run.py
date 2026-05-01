"""
First20 DNA Dry Run CLI

Command-line interface for running the DNA dashboard dry-run mode.

Usage:
    python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 20 --dashboard-stream --no-broadcast

    python -m apex_omega_core.dry_run.run_first20_dna_dry_run --help
"""

import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from apex_omega_core.dry_run import (
    get_dry_run_logger,
    get_block_cycle_index,
    get_dry_run_orchestrator,
    get_realtime_bus,
    DryRunEvent,
)
from apex_omega_core.safety.dry_run_guard import (
    enforce_no_broadcast_env,
    validate_dry_run_safety,
    DryRunBroadcastBlockedError,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run Apex-Omega First20 DNA Dry-Run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 20
  python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 20 --dashboard-stream
  python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 10 --no-broadcast
        """,
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of C1 cycles (default: 20)",
    )
    
    parser.add_argument(
        "--dashboard-stream",
        action="store_true",
        help="Enable dashboard event streaming",
    )
    
    parser.add_argument(
        "--no-broadcast",
        action="store_true",
        help="Enforce no-broadcast safety (default in dry-run)",
    )
    
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Directory for log files (default: logs/)",
    )
    
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate safety configuration, don't run",
    )
    
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current dry-run status",
    )
    
    parser.add_argument(
        "--logs",
        action="store_true",
        help="Show log file paths and contents",
    )
    
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset all dry-run state and logs",
    )
    
    return parser.parse_args()


def validate_safety() -> int:
    """Validate dry-run safety configuration."""
    is_safe, issues = validate_dry_run_safety()
    
    print("=" * 60)
    print("DRY-RUN SAFETY VALIDATION")
    print("=" * 60)
    
    if is_safe:
        print("✓ All safety checks passed")
        return 0
    else:
        print("✗ Safety issues found:")
        for issue in issues:
            print(f"  - {issue}")
        return 1


def show_status() -> int:
    """Show current dry-run status."""
    logger = get_dry_run_logger()
    block_index = get_block_cycle_index()
    realtime_bus = get_realtime_bus()
    
    print("=" * 60)
    print("DRY-RUN STATUS")
    print("=" * 60)
    
    # Logger stats
    stats = logger.get_stats()
    print("\nLogger Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    # Block index state
    state = block_index.get_current_state()
    print("\nBlock Index State:")
    for key, value in state.items():
        print(f"  {key}: {value}")
    
    # Recent events
    events = realtime_bus.get_recent_events(10)
    print(f"\nRecent Events ({len(events)}):")
    for event in events[-5:]:
        print(f"  [{event.get('timestamp', '')}] {event.get('event', '')}")
    
    return 0


def show_logs() -> int:
    """Show log file paths and contents."""
    logger = get_dry_run_logger()
    
    print("=" * 60)
    print("DRY-RUN LOG FILES")
    print("=" * 60)
    
    log_files = [
        ("DNA Cards", logger.dna_cards_path),
        ("Cycle Pairs", logger.cycle_pairs_path),
        ("Block Cycles", logger.block_cycles_path),
        ("Payload Builds", logger.payload_builds_path),
        ("Rejections", logger.rejections_path),
        ("Dashboard Events", logger.dashboard_events_path),
        ("Summary", logger.summary_path),
    ]
    
    for name, path in log_files:
        print(f"\n{name}:")
        print(f"  Path: {path}")
        if path.exists():
            size = path.stat().st_size
            print(f"  Size: {size} bytes")
            
            # Show preview for JSONL files
            if path.suffix == ".jsonl":
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    print(f"  Records: {len(lines)}")
                    if lines:
                        print(f"  Last record: {lines[-1][:200]}...")
        else:
            print("  Status: Not created")
    
    return 0


def reset_state() -> int:
    """Reset all dry-run state and logs."""
    print("=" * 60)
    print("RESETTING DRY-RUN STATE")
    print("=" * 60)
    
    # Reset all singletons
    get_dry_run_logger().reset()
    get_block_cycle_index().reset()
    get_realtime_bus().clear()
    
    print("✓ State reset complete")
    return 0


def run_dry_run(args: argparse.Namespace) -> int:
    """Run the dry-run."""
    # Enforce no-broadcast
    if args.no_broadcast or True:  # Always enforce in CLI
        enforce_no_broadcast_env()
    
    # Validate safety
    is_safe, issues = validate_dry_run_safety()
    if not is_safe:
        print("✗ Safety validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    
    print("=" * 60)
    print("APEX-OMEGA FIRST20 DNA DRY-RUN")
    print("=" * 60)
    print(f"Limit: {args.limit} C1 cycles")
    print(f"Dashboard streaming: {args.dashboard_stream}")
    print(f"No-broadcast: enforced")
    print()
    
    # Get components
    logger = get_dry_run_logger(args.log_dir)
    block_index = get_block_cycle_index(args.log_dir)
    realtime_bus = get_realtime_bus(args.log_dir)
    orchestrator = get_dry_run_orchestrator(
        limit=args.limit,
        scanner_fn=None,
        c1_fn=None,
        c2_fn=None,
        fork_sim_fn=None,
    )
    
    # Subscribe to events for console output
    def event_console_output(event: DryRunEvent, data: dict) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {event.value}: {json.dumps(data)[:100]}")
    
    if args.dashboard_stream:
        realtime_bus.subscribe(event_console_output)
    
    # Start orchestrator
    start_result = orchestrator.start()
    print(f"Started: {start_result}")
    
    # Simulate some cycles (in real implementation, this would call scanner)
    print("\n--- Simulating C1/C2 cycles (demo mode) ---")
    
    for i in range(args.limit):
        # Demo cycle data
        candidate = {
            "block_number": 73491288 + i,
            "route": "WMATIC->USDC->MATIC",
            "spread_bps": 15.0 + i,
        }
        
        c1_result = {
            "accepted": True,
            "simulated_net_usd": 10.0 + (i * 0.5),
            "payload_built": True,
        }
        
        c2_result = {
            "action": "NO_OP" if i % 3 == 0 else "EXECUTE",
            "simulated_net_usd": 0 if i % 3 == 0 else 5.0,
        }
        
        result = orchestrator.run_cycle(candidate, c1_result, c2_result)
        
        if result.get("status") == "completed":
            print(f"  Cycle {i+1}/{args.limit}: {result.get('cycle_status')} - Net: ${result.get('simulated_net', 0):.2f}")
    
    # Get final status
    status = orchestrator.get_status()
    stats = orchestrator.get_stats()
    
    print("\n" + "=" * 60)
    print("DRY-RUN COMPLETE")
    print("=" * 60)
    print(f"Cycles completed: {stats.get('cycles_completed', 0)}")
    print(f"C1 cards: {stats.get('c1_cards', 0)}")
    print(f"C2 cards: {stats.get('c2_cards', 0)}")
    print(f"Total DNA cards: {stats.get('total_dna_cards', 0)}")
    print(f"Cycle pairs: {stats.get('cycle_pairs', 0)}")
    print(f"Rejections: {stats.get('rejections', 0)}")
    
    # Show summary
    summary = logger.read_summary()
    if summary:
        print(f"\nSummary:")
        print(f"  Status: {summary.get('status', 'unknown')}")
        print(f"  Duration: {summary.get('duration_seconds', 0):.1f}s")
    
    print("\nLog files:")
    print(f"  {logger.dna_cards_path}")
    print(f"  {logger.cycle_pairs_path}")
    print(f"  {logger.summary_path}")
    
    print("\n✓ Dry-run complete - NO BROADCAST")
    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()
    
    # Handle special commands
    if args.validate_only:
        return validate_safety()
    
    if args.status:
        return show_status()
    
    if args.logs:
        return show_logs()
    
    if args.reset:
        return reset_state()
    
    # Run dry-run
    return run_dry_run(args)


if __name__ == "__main__":
    sys.exit(main())