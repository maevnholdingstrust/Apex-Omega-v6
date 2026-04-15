from apex_omega_core.core.types import ExecutionResult, Slippage, ArbitrageOpportunity
from apex_omega_core.core.contract_targets import C2_TARGET
from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.slippage_sentinel import SlippageSentinel

class C2SurgeonApex:
    """C2 contract decision logic driven by sentinel slippage variables."""

    def __init__(self):
        self.sentinel = SlippageSentinel()
        self.target_address = C2_TARGET
        self.contract_invoker = ContractInvoker(self.target_address)
        self.flash_providers = ['aave', 'balancer']
        self.provider_fee_bps = {'aave': 9.0, 'balancer': 7.0}
        self.provider_reliability = {'aave': 0.995, 'balancer': 0.992}
        self.max_total_slippage = 0.03

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> ExecutionResult:
        """Execute arbitrage with surgical precision to minimize slippage"""
        try:
            # Calculate optimal timing and sizing
            optimal_size = self._calculate_optimal_size(opportunity)
            flash_provider = self._select_precise_provider(opportunity)

            # Execute with precision
            success = await self._execute_precise_arbitrage(opportunity, optimal_size, flash_provider)

            if success:
                slippage = Slippage(
                    expected_price=opportunity.buy_price,
                    actual_price=opportunity.sell_price,
                    difference=0.0  # Minimal slippage
                )
                return ExecutionResult(success=True, slippage=slippage, tx_hash=self.target_address)
            else:
                return ExecutionResult(success=False)

        except Exception as e:
            print(f"Precise arbitrage execution failed: {e}")
            return ExecutionResult(success=False)

    def decide_contract_action(
        self,
        route,
        raw_spread: float,
        min_input: float,
        max_input: float,
        gas_cost: float,
        pending_txs=None,
        steps: int = 100,
    ):
        """Sentinel -> decide duplicate/reverse/do nothing -> fork validate -> mempool validate."""
        pending = pending_txs or []
        sentinel_output = self.sentinel.build_c2_slippage_context(route, raw_spread, min_input, max_input, steps)
        total_slippage = sum(item['slippage'] for item in sentinel_output['slippage_per_leg'])
        net_profit = sentinel_output['profit'] - gas_cost

        reverse_route = self.sentinel.reverse_route(route)
        reverse_output = self.sentinel.optimize(reverse_route, min_input, max_input, steps=steps, raw_spread=-raw_spread)

        if net_profit <= 0 or total_slippage > self.max_total_slippage:
            decision = 'DO_NOTHING'
        elif reverse_output['profit'] > sentinel_output['profit']:
            decision = 'REVERSE'
        elif sentinel_output['profit'] > gas_cost * 2:
            decision = 'DUPLICATE'
        else:
            decision = 'STRIKE'

        chosen_output = reverse_output if decision == 'REVERSE' else sentinel_output
        chosen_route = reverse_route if decision == 'REVERSE' else route
        fork_validation = self.sentinel.validate_on_fork(chosen_route, chosen_output['optimal_input'])
        mempool_validation = self.sentinel.mempool_validate(
            chosen_route,
            pending,
            chosen_output['optimal_input'],
            chosen_output['final_output'],
        )

        return {
            'decision': decision if mempool_validation['decision'] == 'SAFE' else 'DO_NOTHING',
            'sentinel_output': chosen_output,
            'fork_validation': fork_validation,
            'mempool_validation': mempool_validation,
            'target_address': self.target_address,
        }

    async def execute_contract_decision(self, decision_plan: dict) -> ExecutionResult:
        """Execute validated C2 decision plan when it calls for action."""
        decision = decision_plan.get('decision')
        if decision not in {'STRIKE', 'DUPLICATE', 'REVERSE'}:
            return ExecutionResult(success=False)

        sentinel_output = decision_plan['sentinel_output']
        calldata = self.contract_invoker.build_c2_calldata(decision_plan)
        invocation = self.contract_invoker.invoke(calldata)
        if not invocation.get('success'):
            return ExecutionResult(success=False)

        slippage = self.sentinel.build_execution_slippage(sentinel_output)
        return ExecutionResult(
            success=True,
            slippage=slippage,
            tx_hash=invocation.get('tx_hash') or self.target_address,
        )

    def _calculate_optimal_size(self, opportunity: ArbitrageOpportunity) -> float:
        """Calculate deterministic trade size to minimize price impact."""
        min_tvl = min(opportunity.buy_pool.tvl_usd, opportunity.sell_pool.tvl_usd)
        # Cap at 2% of weaker pool TVL and never exceed suggested flash amount.
        tvl_cap = min_tvl * 0.02
        conservative_size = min(opportunity.flash_loan_amount, tvl_cap)
        return max(5000.0, conservative_size)

    def _select_precise_provider(self, opportunity: ArbitrageOpportunity) -> str:
        """Select provider with lowest risk-adjusted cost using deterministic scoring."""
        best_provider = self.flash_providers[0]
        best_score = float('-inf')

        for provider in self.flash_providers:
            fee_bps = self.provider_fee_bps.get(provider, 15.0)
            reliability = self.provider_reliability.get(provider, 0.98)
            # Surgeon prioritizes reliability first, then fee.
            score = (reliability * 100.0) - (fee_bps * 0.8)

            if score > best_score:
                best_score = score
                best_provider = provider
            elif score == best_score and provider < best_provider:
                best_provider = provider

        return best_provider

    async def _execute_precise_arbitrage(self, opportunity: ArbitrageOpportunity,
                                       size: float, provider: str) -> bool:
        """Execute arbitrage by invoking the C2 decision contract."""
        route = [
            {
                'venue': opportunity.buy_pool.dex,
                'pair': f"{opportunity.buy_pool.token1} → {opportunity.buy_pool.token0}",
                'reserve_in': max(opportunity.buy_pool.tvl_usd, 1.0),
                'reserve_out': max(opportunity.buy_pool.tvl_usd / max(opportunity.buy_price, 1e-9), 1.0),
                'fee': opportunity.buy_pool.fee,
                'price_in_usd': 1.0,
                'price_out_usd': max(opportunity.buy_price, 1e-9),
                'tvl_usd': max(opportunity.buy_pool.tvl_usd, 1.0),
                'volume_24h_usd': max(opportunity.buy_pool.tvl_usd * 0.5, 1.0),
                'age_in_blocks': 120.0,
            },
            {
                'venue': opportunity.sell_pool.dex,
                'pair': f"{opportunity.sell_pool.token0} → {opportunity.sell_pool.token1}",
                'reserve_in': max(opportunity.sell_pool.tvl_usd / max(opportunity.sell_price, 1e-9), 1.0),
                'reserve_out': max(opportunity.sell_pool.tvl_usd, 1.0),
                'fee': opportunity.sell_pool.fee,
                'price_in_usd': max(opportunity.sell_price, 1e-9),
                'price_out_usd': 1.0,
                'tvl_usd': max(opportunity.sell_pool.tvl_usd, 1.0),
                'volume_24h_usd': max(opportunity.sell_pool.tvl_usd * 0.5, 1.0),
                'age_in_blocks': 120.0,
            },
        ]
        raw_spread = float(opportunity.sell_price - opportunity.buy_price)
        decision_plan = self.decide_contract_action(
            route,
            raw_spread=raw_spread,
            min_input=max(1.0, size * 0.5),
            max_input=max(2.0, size),
            gas_cost=max(1.0, opportunity.gas_estimate),
            pending_txs=[],
            steps=32,
        )
        if decision_plan.get('decision') not in {'STRIKE', 'DUPLICATE', 'REVERSE'}:
            return False

        calldata = self.contract_invoker.build_c2_calldata(decision_plan)
        invocation = self.contract_invoker.invoke(calldata)
        return bool(invocation.get('success'))

    # Legacy method for backward compatibility
    def execute(self, order: dict) -> ExecutionResult:
        """Legacy execution method"""
        slippage = Slippage(expected_price=order.get('price', 0), actual_price=order.get('price', 0), difference=0.0)
        return ExecutionResult(success=True, slippage=slippage)