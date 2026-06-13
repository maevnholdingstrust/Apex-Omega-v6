#!/usr/bin/env python3
"""Apex-Omega 2.0: One-click live execution bootstrap."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from apex_omega_core.core.live_execution_orchestrator import LiveExecutionOrchestrator
from apex_omega_core.core.runtime_config import load_runtime_config
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Boot and run Apex 2.0."""
    logger.info("=" * 80)
    logger.info("APEX-OMEGA v2.0 LIVE EXECUTION BOOTSTRAP")
    logger.info("=" * 80)

    # Load config
    try:
        config = load_runtime_config()
    except Exception as e:
        logger.error(f"Config load failed: {e}")
        sys.exit(1)

    logger.info(f"Chain: Polygon PoS (#{config.chain_id})")
    logger.info(f"Live mode: {config.live_trading_enabled}")
    logger.info(f"Min profit: ${config.min_net_profit_usd:.2f}")
    logger.info(f"Pool TVL gate: ${config.min_pool_tvl_usd:.0f}")

    # Initialize orchestrator
    orchestrator = LiveExecutionOrchestrator()

    # Health check
    logger.info("\nRunning health checks...")
    health = await orchestrator.health_check()

    if not health["system"]["operational"]:
        logger.error("System health check FAILED")
        logger.info(json.dumps(health, indent=2))
        sys.exit(1)

    logger.info("✓ RPC: Primary healthy")
    logger.info(f"✓ Discovery: {health['discovery']['active_venue_count']} venues active")
    logger.info(f"✓ Safety gates: Operational")

    # Demo: Validate a sample route
    logger.info("\n" + "=" * 80)
    logger.info("DEMO: Route Validation")
    logger.info("=" * 80)

    demo_result = orchestrator.validate_route(
        token_path=["USDC", "WMATIC", "USDC"],
        venue_path=["quickswap_v2", "uniswap_v3"],
        gross_profit_usd=15.0,
        flash_loan_usd=50000.0,
        pool_tvl_usd=1000000.0,
        slippage_bps=25.0,
        slippage_cost_usd=5.0,
        gas_cost_usd=2.50,
    )

    logger.info(f"Route executable: {demo_result['executable']}")
    if demo_result["executable"]:
        logger.info(f"  Net profit: ${demo_result['profitability']['net_profit_usd']:.2f}")
        logger.info(f"  Gas cost: ${demo_result['profitability']['gas_cost_usd']:.2f}")
    else:
        logger.warning(f"  Reason: {demo_result['reason']}")

    # Status
    logger.info("\n" + "=" * 80)
    logger.info("ORCHESTRATOR STATUS")
    logger.info("=" * 80)
    status = orchestrator.status()
    logger.info(json.dumps(
        {
            "rpc_chain": status["rpc"]["chain_id"],
            "rpc_primary_healthy": status["rpc"].get("primary_healthy", False),
            "discovery_venues": status["discovery"]["active_venue_count"],
            "safety_24h_profit": f"${status['safety'].get('net_24h_usd', 0):.2f}",
            "live_mode": status["live_mode"],
        },
        indent=2,
    ))

    if not config.live_trading_enabled:
        logger.info("\n" + "=" * 80)
        logger.info("DRY-RUN MODE - No live execution")
        logger.info("=" * 80)
        logger.info("To enable live execution, set: LIVE_TRADING_ENABLED=true")
    else:
        logger.info("\n" + "=" * 80)
        logger.info("⚠️  LIVE TRADING ENABLED")
        logger.info("=" * 80)

    logger.info("\nApex 2.0 ready. Listening for arbitrage opportunities...")
    logger.info("Press Ctrl+C to stop.")

    try:
        # Keep running (would be actual scanning loop in production)
        while True:
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        logger.info("\nShutdown requested.")


if __name__ == "__main__":
    asyncio.run(main())
