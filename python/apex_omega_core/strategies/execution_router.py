from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
from apex_omega_core.core.types import ExecutionResult, ArbitrageOpportunity
from apex_omega_core.core.mev_gas_oracle import GasOracle, TipOptimizer

class ExecutionRouter:
    """Smart decision engine for arbitrage execution strategies"""

    #: Default gas units assumed for Polygon flash-loan arbitrage transactions.
    DEFAULT_GAS_UNITS: int = 350_000
    #: Polygon MATIC price used to convert gas costs to USD when a live feed is unavailable.
    DEFAULT_MATIC_PRICE_USD: float = 0.85

    def __init__(self):
        self.strategies = {
            'aggressor': C1AggressorApex(),
            'surgeon': C2SurgeonApex()
        }
        self.aggressor_profit_threshold_usd = 100.0
        self.aggressor_spread_threshold_bps = 120.0
        self._gas_oracle = GasOracle()

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
        gas_cost: float = 0.0,
        pending_txs=None,
        min_input: float = 1000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
        p_net_usd: float = 0.0,
    ):
        """Corrected pipeline: discovery -> sentinel -> C1/C2 -> fork+mempool validate -> execution.

        Gas cost is now derived from the live :class:`~.mev_gas_oracle.GasOracle`
        when ``gas_cost`` is not explicitly provided (i.e. is ``0.0``).  The
        :class:`~.mev_gas_oracle.TipOptimizer` also computes the EIP-1559
        ``maxPriorityFeePerGas`` that maximises ``P(fill) × P_net``, which is
        attached to the returned result dict under ``"eip1559_params"``.
        """
        pending = pending_txs or []

        # Derive live gas cost when not supplied by the caller.
        effective_gas_cost = gas_cost
        eip1559_params: dict = {}
        try:
            snapshot = self._gas_oracle.get_snapshot()
            optimizer = TipOptimizer(
                snapshot,
                gas_units=self.DEFAULT_GAS_UNITS,
            )
            if effective_gas_cost <= 0.0:
                effective_gas_cost = optimizer.gas_cost_usd(snapshot.tip_p50_gwei)
            eip1559_params = optimizer.build_eip1559_params(p_net_usd)
        except Exception:
            # Non-fatal: fall back to caller-supplied gas_cost (or 0).
            pass

        # --- Punch 1: C1 (Aggressor) works on the pre-trade state ---
        c1 = self.strategies['aggressor'].prepare_contract_strike(
            route,
            raw_spread,
            min_input,
            max_input,
            pending,
            steps,
        )
        c1_execution = await self.strategies['aggressor'].execute_contract_strike(c1)

        # --- State mutation: apply C1's trade to derive the post-trade reserves ---
        # C2 must NEVER evaluate the same state as C1; doing so would double-count
        # the edge, cause overtrading, and destroy profitability.
        sentinel = self.strategies['aggressor'].sentinel
        post_route, post_spread = sentinel.apply_post_trade_state(
            route,
            c1['sentinel_output'],
        )

        # --- Punch 2 (optional): C2 (Surgeon) works on the post-trade state ---
        c2 = self.strategies['surgeon'].decide_contract_action(
            post_route,
            post_spread,
            min_input,
            max_input,
            effective_gas_cost,
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
                'post_trade_spread': post_spread,
            },
            'eip1559_params': eip1559_params,
            'gas_cost_usd': effective_gas_cost,
        }