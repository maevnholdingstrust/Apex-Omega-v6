from apex_omega_core.core.types import ExecutionResult, Slippage, ArbitrageOpportunity
from apex_omega_core.core.contract_targets import C1_TARGET
from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.slippage_sentinel import SlippageSentinel

class C1AggressorApex:
    """C1 contract strike logic driven by sentinel optimization."""

    def __init__(self):
        self.sentinel = SlippageSentinel()
        self.target_address = C1_TARGET
        self.contract_invoker = ContractInvoker(self.target_address)
        self.flash_providers = ['aave', 'balancer']
        self.provider_fee_bps = {'aave': 9.0, 'balancer': 7.0}
        self.provider_latency_ms = {'aave': 120.0, 'balancer': 180.0}
        self.min_expected_profit_usd = 10.0

    def prepare_contract_strike(
        self,
        route,
        raw_spread: float,
        min_input: float,
        max_input: float,
        pending_txs=None,
        steps: int = 100,
    ):
        """Discovery -> sentinel optimize -> fork validate -> mempool validate for C1."""
        pending = pending_txs or []
        sentinel_output = self.sentinel.build_c1_slippage_context(route, raw_spread, min_input, max_input, steps)
        fork_validation = self.sentinel.validate_on_fork(route, sentinel_output['optimal_input'])
        mempool_validation = self.sentinel.mempool_validate(
            route,
            pending,
            sentinel_output['optimal_input'],
            sentinel_output['final_output'],
        )
        return {
            'sentinel_output': sentinel_output,
            'fork_validation': fork_validation,
            'mempool_validation': mempool_validation,
            'target_address': self.target_address,
            'action': 'STRIKE' if sentinel_output['profit'] > 0 and mempool_validation['decision'] == 'SAFE' else 'ABORT',
        }

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> ExecutionResult:
        """Execute arbitrage opportunity with maximum speed using flash loans"""
        try:
            # Select best flash loan provider
            flash_provider = self._select_flash_provider(opportunity)

            # Execute flash loan arbitrage
            success = await self._execute_flash_arbitrage(opportunity, flash_provider)

            if success:
                slippage = Slippage(
                    expected_price=opportunity.buy_price,
                    actual_price=opportunity.sell_price,
                    difference=opportunity.sell_price - opportunity.buy_price
                )
                return ExecutionResult(success=True, slippage=slippage, tx_hash=self.target_address)
            else:
                return ExecutionResult(success=False)

        except Exception as e:
            print(f"Arbitrage execution failed: {e}")
            return ExecutionResult(success=False)

    async def execute_contract_strike(self, strike_plan: dict) -> ExecutionResult:
        """Execute validated C1 strike plan."""
        sentinel_output = strike_plan['sentinel_output']
        if strike_plan.get('action') != 'STRIKE':
            return ExecutionResult(success=False)

        calldata = self.contract_invoker.build_c1_calldata(strike_plan)
        invocation = self.contract_invoker.invoke(calldata)
        if not invocation.get('success'):
            return ExecutionResult(success=False)

        slippage = self.sentinel.build_execution_slippage(sentinel_output)
        return ExecutionResult(
            success=True,
            slippage=slippage,
            tx_hash=invocation.get('tx_hash') or self.target_address,
        )

    def _select_flash_provider(self, opportunity: ArbitrageOpportunity) -> str:
        """Select optimal flash loan provider using deterministic scoring."""
        best_provider = self.flash_providers[0]
        best_score = float('-inf')

        for provider in self.flash_providers:
            fee_bps = self.provider_fee_bps.get(provider, 15.0)
            latency_ms = self.provider_latency_ms.get(provider, 250.0)
            spread_component = min(opportunity.spread_bps, 500.0)
            profit_component = min(opportunity.estimated_profit_usd / 10.0, 100.0)
            # Aggressor prefers low latency and accepts slightly higher fee when spread is large.
            score = (spread_component * 0.6) + (profit_component * 0.3) - (latency_ms * 0.08) - (fee_bps * 0.15)

            if score > best_score:
                best_score = score
                best_provider = provider
            elif score == best_score and provider < best_provider:
                # Deterministic tie-breaker.
                best_provider = provider

        return best_provider

    async def _execute_flash_arbitrage(self, opportunity: ArbitrageOpportunity, provider: str) -> bool:
        """Execute flash-loan arbitrage by invoking C1 target contract."""
        if opportunity.estimated_profit_usd < self.min_expected_profit_usd:
            return False

        route = [
            {
                'venue': opportunity.buy_pool.dex,
                'pair': f"{opportunity.buy_pool.token1} → {opportunity.buy_pool.token0}",
                'reserve_in': max(opportunity.buy_pool.tvl_usd, 1.0),
                'reserve_out': max(opportunity.buy_pool.tvl_usd, 1.0),
                'fee': opportunity.buy_pool.fee,
            },
            {
                'venue': opportunity.sell_pool.dex,
                'pair': f"{opportunity.sell_pool.token0} → {opportunity.sell_pool.token1}",
                'reserve_in': max(opportunity.sell_pool.tvl_usd, 1.0),
                'reserve_out': max(opportunity.sell_pool.tvl_usd, 1.0),
                'fee': opportunity.sell_pool.fee,
            },
        ]
        raw_spread = float(opportunity.sell_price - opportunity.buy_price)
        strike_plan = self.prepare_contract_strike(
            route,
            raw_spread=raw_spread,
            min_input=max(1.0, opportunity.flash_loan_amount * 0.5),
            max_input=max(2.0, opportunity.flash_loan_amount),
            pending_txs=[],
            steps=32,
        )
        if strike_plan.get('action') != 'STRIKE':
            return False

        calldata = self.contract_invoker.build_c1_calldata(strike_plan)
        invocation = self.contract_invoker.invoke(calldata)
        return bool(invocation.get('success'))

    # Legacy method for backward compatibility
    def execute(self, order: dict) -> ExecutionResult:
        """Legacy execution method"""
        slippage = Slippage(expected_price=order.get('price', 0), actual_price=order.get('price', 0) + 0.01, difference=0.01)
        return ExecutionResult(success=True, slippage=slippage)