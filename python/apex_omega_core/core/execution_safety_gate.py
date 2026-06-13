"""ExecutionSafetyGate: Position limits, confirmation gates, rollback protection."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SafetyCheckStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class SafetyCheck:
    name: str
    status: SafetyCheckStatus
    message: str


@dataclass
class PositionLimits:
    """Per-cycle position sizing constraints."""

    max_flash_loan_usd: float = 100000.0
    max_trade_to_pool_ratio_bps: int = 500  # 5% of pool
    max_position_usd_per_cycle: float = 50000.0
    min_position_usd: float = 100.0


@dataclass
class ExecutionSafetyGate:
    """Gate all live executions through safety checks."""

    position_limits: PositionLimits = field(default_factory=PositionLimits)
    last_execution_ts: float = 0.0
    execution_count_24h: int = 0
    total_profit_24h_usd: float = 0.0
    total_loss_24h_usd: float = 0.0
    active_positions: int = 0

    def __post_init__(self):
        # Load from environment
        self.position_limits = PositionLimits(
            max_flash_loan_usd=float(os.getenv("AUTONOMOUS_MAX_FLASHLOAN_CAP_USD", "100000")),
            max_trade_to_pool_ratio_bps=int(os.getenv("MAX_TRADE_TO_POOL_RATIO_BPS", "500")),
            max_position_usd_per_cycle=float(os.getenv("APEX_MAX_POSITION_USD", "50000")),
            min_position_usd=float(os.getenv("MIN_POSITION_USD", "100")),
        )

    def check_position_size(self, size_usd: float) -> SafetyCheck:
        """Verify position size is within limits."""
        if size_usd < self.position_limits.min_position_usd:
            return SafetyCheck(
                name="position_size_min",
                status=SafetyCheckStatus.FAIL,
                message=f"position_usd={size_usd} < min {self.position_limits.min_position_usd}",
            )

        if size_usd > self.position_limits.max_position_usd_per_cycle:
            return SafetyCheck(
                name="position_size_max",
                status=SafetyCheckStatus.FAIL,
                message=f"position_usd={size_usd} > max {self.position_limits.max_position_usd_per_cycle}",
            )

        return SafetyCheck(
            name="position_size",
            status=SafetyCheckStatus.PASS,
            message=f"position_usd={size_usd} OK",
        )

    def check_pool_impact(self, trade_size_usd: float, pool_tvl_usd: float) -> SafetyCheck:
        """Verify trade doesn't exceed pool impact threshold."""
        if pool_tvl_usd <= 0:
            return SafetyCheck(
                name="pool_tvl",
                status=SafetyCheckStatus.FAIL,
                message="pool_tvl_usd is zero or negative",
            )

        ratio_bps = int((trade_size_usd / pool_tvl_usd) * 10000)

        if ratio_bps > self.position_limits.max_trade_to_pool_ratio_bps:
            return SafetyCheck(
                name="pool_impact",
                status=SafetyCheckStatus.FAIL,
                message=f"trade_ratio={ratio_bps} bps > max {self.position_limits.max_trade_to_pool_ratio_bps} bps",
            )

        return SafetyCheck(
            name="pool_impact",
            status=SafetyCheckStatus.PASS,
            message=f"trade_ratio={ratio_bps} bps OK",
        )

    def check_flash_loan_size(self, flash_amount_usd: float) -> SafetyCheck:
        """Verify flash loan amount is within limits."""
        if flash_amount_usd > self.position_limits.max_flash_loan_usd:
            return SafetyCheck(
                name="flash_loan_size",
                status=SafetyCheckStatus.FAIL,
                message=f"flash_usd={flash_amount_usd} > max {self.position_limits.max_flash_loan_usd}",
            )

        return SafetyCheck(
            name="flash_loan_size",
            status=SafetyCheckStatus.PASS,
            message=f"flash_usd={flash_amount_usd} OK",
        )

    def check_rate_limit(self, min_interval_sec: float = 5.0) -> SafetyCheck:
        """Prevent execution spam."""
        now = time.time()
        if self.last_execution_ts > 0 and (now - self.last_execution_ts) < min_interval_sec:
            return SafetyCheck(
                name="rate_limit",
                status=SafetyCheckStatus.FAIL,
                message=f"last execution {now - self.last_execution_ts:.1f}s ago, min {min_interval_sec}s required",
            )

        return SafetyCheck(
            name="rate_limit",
            status=SafetyCheckStatus.PASS,
            message=f"rate limit OK",
        )

    def check_daily_drawdown(self, max_loss_usd: float = 500.0) -> SafetyCheck:
        """Circuit breaker on daily losses."""
        if self.total_loss_24h_usd > max_loss_usd:
            return SafetyCheck(
                name="daily_loss_limit",
                status=SafetyCheckStatus.FAIL,
                message=f"24h loss_usd={self.total_loss_24h_usd:.2f} > max {max_loss_usd}",
            )

        return SafetyCheck(
            name="daily_loss_limit",
            status=SafetyCheckStatus.PASS,
            message=f"24h loss OK",
        )

    def pre_execution_checks(
        self,
        size_usd: float,
        flash_loan_usd: float,
        pool_tvl_usd: float,
    ) -> Dict[str, Any]:
        """Run all pre-execution safety checks."""

        checks = [
            self.check_position_size(size_usd),
            self.check_pool_impact(size_usd, pool_tvl_usd),
            self.check_flash_loan_size(flash_loan_usd),
            self.check_rate_limit(),
            self.check_daily_drawdown(),
        ]

        passed = all(c.status == SafetyCheckStatus.PASS for c in checks)
        failures = [c for c in checks if c.status == SafetyCheckStatus.FAIL]

        return {
            "all_passed": passed,
            "checks": [
                {"name": c.name, "status": c.status.value, "message": c.message}
                for c in checks
            ],
            "failures": [c.message for c in failures],
            "executable": passed and len(failures) == 0,
        }

    def record_execution(self, profit_usd: float) -> None:
        """Record execution outcome."""
        self.last_execution_ts = time.time()
        self.execution_count_24h += 1
        self.active_positions += 1

        if profit_usd > 0:
            self.total_profit_24h_usd += profit_usd
            logger.info(f"✓ Execution recorded: +{profit_usd:.2f} USD")
        else:
            self.total_loss_24h_usd += abs(profit_usd)
            logger.warning(f"✗ Execution recorded: {profit_usd:.2f} USD")

    def status(self) -> Dict[str, Any]:
        """Return safety gate status."""
        return {
            "execution_count_24h": self.execution_count_24h,
            "total_profit_24h_usd": self.total_profit_24h_usd,
            "total_loss_24h_usd": self.total_loss_24h_usd,
            "net_24h_usd": self.total_profit_24h_usd - self.total_loss_24h_usd,
            "active_positions": self.active_positions,
            "last_execution_ts": self.last_execution_ts,
            "position_limits": {
                "max_flash_loan_usd": self.position_limits.max_flash_loan_usd,
                "max_trade_to_pool_ratio_bps": self.position_limits.max_trade_to_pool_ratio_bps,
                "max_position_usd_per_cycle": self.position_limits.max_position_usd_per_cycle,
            },
        }
