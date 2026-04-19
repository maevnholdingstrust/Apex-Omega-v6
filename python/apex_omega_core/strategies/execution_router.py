from apex_omega_core.strategies.c1_aggressor_apex import C1AggressorApex
from apex_omega_core.strategies.c2_surgeon_apex import C2SurgeonApex
from apex_omega_core.strategies.dual_punch import DualPunchEngine, DualPunchParams, DualPunchCycleResult
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
        self._dual_punch = DualPunchEngine()

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
            },
            'eip1559_params': eip1559_params,
            'gas_cost_usd': effective_gas_cost,
        }

    def run_dual_punch_cycle(
        self,
        route,
        params: DualPunchParams = None,
        alternate_routes=None,
        min_input: float = 1_000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
        raw_spread: float = 0.0,
        gas_cost_usd: float = 0.0,
        p_net_usd: float = 0.0,
    ) -> DualPunchCycleResult:
        """Run a full Dual Punch cycle using the live gas oracle for cost estimates.

        When ``params`` is not provided a default :class:`DualPunchParams` is
        constructed with gas costs derived from the live :class:`GasOracle`.

        Parameters
        ----------
        route:
            List of route-leg dicts representing the live market state s0.
        params:
            Optional pre-built :class:`DualPunchParams`.  When ``None``, a
            default instance is created with gas costs from the live oracle.
        alternate_routes:
            Additional route variants for Punch 2 Module C evaluation.
        min_input / max_input / steps:
            Size-search bounds passed to the sentinel optimizer.
        raw_spread:
            Observed raw spread (used for optimizer context).
        gas_cost_usd:
            Override gas cost in USD.  When ``0.0`` (default) the live oracle
            provides the estimate.
        p_net_usd:
            Expected net P&L passed to the EIP-1559 tip optimizer.

        Returns
        -------
        :class:`DualPunchCycleResult`
        """
        effective_gas_cost = gas_cost_usd
        try:
            snapshot = self._gas_oracle.get_snapshot()
            optimizer = TipOptimizer(snapshot, gas_units=self.DEFAULT_GAS_UNITS)
            if effective_gas_cost <= 0.0:
                effective_gas_cost = optimizer.gas_cost_usd(snapshot.tip_p50_gwei)
        except Exception:
            pass

        if params is None:
            params = DualPunchParams(
                gas_cost1=effective_gas_cost,
                gas_cost2=effective_gas_cost,
            )
        else:
            # Propagate oracle-derived gas when the caller left costs at zero.
            if params.gas_cost1 <= 0.0:
                params = DualPunchParams(
                    **{**params.__dict__, 'gas_cost1': effective_gas_cost}
                )
            if params.gas_cost2 <= 0.0:
                params = DualPunchParams(
                    **{**params.__dict__, 'gas_cost2': effective_gas_cost}
                )

        return self._dual_punch.run_dual_punch_cycle(
            route=route,
            params=params,
            alternate_routes=alternate_routes,
            min_input=min_input,
            max_input=max_input,
            steps=steps,
            raw_spread=raw_spread,
        )