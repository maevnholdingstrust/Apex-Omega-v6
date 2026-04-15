from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
from apex_omega_core.core.types import ExecutionResult, ArbitrageOpportunity

class ExecutionRouter:
    """Smart decision engine for arbitrage execution strategies"""

    def __init__(self):
        self.strategies = {
            'aggressor': C1AggressorApex(),
            'surgeon': C2SurgeonApex()
        }
        self.aggressor_profit_threshold_usd = 100.0
        self.aggressor_spread_threshold_bps = 120.0

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> ExecutionResult:
        """Route arbitrage opportunity to optimal strategy"""
        # Select strategy based on opportunity characteristics
        strategy = self._select_strategy(opportunity)

        if strategy in self.strategies:
            return await self.strategies[strategy].execute_arbitrage(opportunity)
        return ExecutionResult(success=False)

    def _select_strategy(self, opportunity: ArbitrageOpportunity) -> str:
        """Select execution strategy based on deterministic opportunity thresholds."""
        if (
            opportunity.estimated_profit_usd >= self.aggressor_profit_threshold_usd
            or opportunity.spread_bps >= self.aggressor_spread_threshold_bps
        ):
            return 'aggressor'
        return 'surgeon'

    # Legacy method for backward compatibility
    def route(self, order: dict, strategy: str) -> ExecutionResult:
        """Legacy routing method"""
        if strategy in self.strategies:
            return self.strategies[strategy].execute(order)
        return ExecutionResult(success=False)

    async def process_discovery_pipeline(
        self,
        route,
        raw_spread: float,
        gas_cost: float,
        pending_txs=None,
        min_input: float = 1000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
    ):
        """Corrected pipeline: discovery -> sentinel -> C1/C2 -> fork+mempool validate -> execution."""
        pending = pending_txs or []
        c1 = self.strategies['aggressor'].prepare_contract_strike(
            route,
            raw_spread,
            min_input,
            max_input,
            pending,
            steps,
        )
        c1_execution = await self.strategies['aggressor'].execute_contract_strike(c1)

        c2 = self.strategies['surgeon'].decide_contract_action(
            route,
            raw_spread,
            min_input,
            max_input,
            gas_cost,
            pending,
            steps,
        )
        c2_execution = await self.strategies['surgeon'].execute_contract_decision(c2)

        return {
            'c1': {
                'plan': c1,
                'execution': c1_execution,
            },
            'c2': {
                'plan': c2,
                'execution': c2_execution,
            },
        }