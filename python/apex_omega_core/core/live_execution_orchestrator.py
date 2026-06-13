"""LiveExecutionOrchestrator: Ties all 2.0 components together."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional
import logging

from .rpc_fanout_bridge import RpcFanoutBridge, RpcFanoutSnapshot
from .execution_gateway import ExecutionGateway, ProfitabilityResult
from .atomic_execution_coordinator import AtomicExecutionCoordinator, C1C2State
from .execution_safety_gate import ExecutionSafetyGate
from .max_venue_discovery import MaxVenueDiscoveryConfig

logger = logging.getLogger(__name__)


class LiveExecutionOrchestrator:
    """Orchestrates live Polygon arbitrage at 2.0 level."""

    def __init__(self):
        self.rpc = RpcFanoutBridge()
        self.gateway = ExecutionGateway()
        self.coordinator = AtomicExecutionCoordinator()
        self.safety = ExecutionSafetyGate()
        self.discovery = MaxVenueDiscoveryConfig()

        self.live_mode_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
        self.dry_run_enabled = os.getenv("DRY_RUN", "true").lower() == "true"

        logger.info(
            f"LiveExecutionOrchestrator initialized | "
            f"live_mode={self.live_mode_enabled} | "
            f"dry_run={self.dry_run_enabled}"
        )

    async def health_check(self) -> Dict[str, Any]:
        """Full system health check."""
        rpc_health = await self.rpc.health_check()

        return {
            "timestamp": __import__("time").time(),
            "rpc": {
                "chain_id": rpc_health.chain_id,
                "primary_healthy": rpc_health.primary_healthy,
                "http_ready": rpc_health.http_ready_count,
                "fallbacks_available": rpc_health.fallbacks_available,
                "last_block": rpc_health.last_block,
            },
            "gateway": self.gateway.status(),
            "discovery": self.discovery.status(),
            "safety": self.safety.status(),
            "coordinator": self.coordinator.cycle_summary(),
            "system": {
                "live_mode_enabled": self.live_mode_enabled,
                "dry_run_enabled": self.dry_run_enabled,
                "operational": rpc_health.primary_healthy and not rpc_health.chain_id != 137,
            },
        }

    def validate_route(
        self,
        token_path: list[str],
        venue_path: list[str],
        gross_profit_usd: float,
        flash_loan_usd: float,
        pool_tvl_usd: float,
        slippage_bps: float,
        slippage_cost_usd: float,
        gas_cost_usd: float,
    ) -> Dict[str, Any]:
        """Validate route through all gates (profitability + safety)."""

        # Profitability gate
        profit_result = self.gateway.validate_profitability(
            gross_profit_usd=gross_profit_usd,
            gas_cost_usd=gas_cost_usd,
            flash_fee_usd=flash_loan_usd * 0.0005,  # 0.05% Aave flashloan fee
            slippage_bps=slippage_bps,
            slippage_cost_usd=slippage_cost_usd,
            pool_tvl_usd=pool_tvl_usd,
        )

        if not profit_result.executable:
            return {
                "executable": False,
                "reason": profit_result.reason,
                "profitability_result": {
                    "gross_profit_usd": profit_result.gross_profit_usd,
                    "net_profit_usd": profit_result.net_profit_usd,
                    "reason": profit_result.reason,
                },
            }

        # Safety gates
        safety_checks = self.safety.pre_execution_checks(
            size_usd=gross_profit_usd,
            flash_loan_usd=flash_loan_usd,
            pool_tvl_usd=pool_tvl_usd,
        )

        if not safety_checks["executable"]:
            return {
                "executable": False,
                "reason": f"safety_gate_failed: {safety_checks['failures']}",
                "safety_checks": safety_checks,
            }

        # All gates passed
        return {
            "executable": True,
            "reason": "all_gates_passed",
            "profitability": {
                "gross_profit_usd": profit_result.gross_profit_usd,
                "net_profit_usd": profit_result.net_profit_usd,
                "gas_cost_usd": profit_result.gas_cost_usd,
                "flash_fee_usd": profit_result.flash_fee_usd,
            },
            "safety_checks": safety_checks,
            "route": {
                "token_path": token_path,
                "venue_path": venue_path,
            },
        }

    def execute_c1(
        self,
        token_path: list[str],
        venue_path: list[str],
        calldata: bytes,
        expected_profit_usd: float,
    ) -> Dict[str, Any]:
        """Execute C1 cycle."""

        if not self.live_mode_enabled:
            logger.warning("C1 skipped: live_mode_enabled=false")
            return {"status": "skipped", "reason": "live_mode_disabled", "simulated": True}

        # Commit route
        commitment = self.coordinator.commit_route(token_path, venue_path, calldata)

        # Fork simulation (would be Rust in production)
        sim_result = self.gateway.fork_simulation_gate(
            route_steps=[],
            calldata=calldata,
            expected_profit_usd=expected_profit_usd,
        )

        if not sim_result.get("executable", False):
            self.coordinator.record_c1_failure(f"simulation: {sim_result.get('reason')}")
            return {
                "status": "failed",
                "reason": f"fork_simulation_failed: {sim_result.get('reason')}",
            }

        # Record execution (in production, this would be real tx hash from relay)
        receipt = self.coordinator.record_c1_execution(
            tx_hash="0x" + "0" * 64,  # Placeholder for dry-run
            block=0,
            gas_used=self.coordinator.C1_GAS_ESTIMATE,
            profit_usd=expected_profit_usd,
        )

        logger.info(f"✓ C1 executed (simulated): {commitment.route_id}")

        return {
            "status": "executed",
            "route_id": commitment.route_id,
            "tx_hash": receipt.tx_hash,
            "profit_usd": receipt.profit_usd,
        }

    def evaluate_c2_trigger(self) -> bool:
        """Decide whether to execute C2."""
        c1_post_state = {"profit_usd": 2.5}  # Placeholder
        return self.coordinator.trigger_c2(c1_post_state)

    def execute_c2(self, expected_profit_usd: float) -> Dict[str, Any]:
        """Execute C2 cycle."""

        if not self.live_mode_enabled:
            return {"status": "skipped", "reason": "live_mode_disabled"}

        receipt = self.coordinator.record_c2_execution(
            tx_hash="0x" + "1" * 64,  # Placeholder for dry-run
            block=0,
            gas_used=self.coordinator.C2_GAS_ESTIMATE,
            profit_usd=expected_profit_usd,
        )

        logger.info(f"✓ C2 executed: {receipt.tx_hash}")

        return {
            "status": "executed",
            "tx_hash": receipt.tx_hash,
            "profit_usd": receipt.profit_usd,
        }

    def finalize_cycle(self) -> Dict[str, Any]:
        """Get cycle summary and reset."""
        summary = self.coordinator.cycle_summary()

        if summary["total_profit_usd"] > 0:
            self.safety.record_execution(summary["total_profit_usd"])

        self.coordinator.reset_cycle()
        return summary

    def status(self) -> Dict[str, Any]:
        """Full orchestrator status."""
        return {
            "rpc": self.rpc.status(),
            "gateway": self.gateway.status(),
            "discovery": self.discovery.status(),
            "safety": self.safety.status(),
            "coordinator": self.coordinator.cycle_summary(),
            "live_mode": self.live_mode_enabled,
            "dry_run": self.dry_run_enabled,
        }
