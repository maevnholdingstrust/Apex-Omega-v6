import logging
from typing import Any, Dict, List

from apex_omega_core.core.types import ExecutionResult, Slippage, ArbitrageOpportunity
from apex_omega_core.core.contract_targets import C2_TARGET
from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.inference import profitability_gate

logger = logging.getLogger(__name__)

# Sentinel value used for age_in_blocks when the pool snapshot age is unknown.
# Zero signals "no stale-data penalty" rather than misrepresenting the data as
# 120 blocks old (≈ 4 min on Polygon).
_FRESHNESS_UNKNOWN_AGE_BLOCKS: float = 0.0

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
        """Execute arbitrage with surgical precision to minimize slippage.

        Derives live P(fill) from the gas oracle, builds a sentinel decision
        plan, and delegates to :meth:`execute_contract_decision` so the real
        on-chain tx hash is propagated back to the caller.
        """
        try:
            try:
                from apex_omega_core.core.mev_gas_oracle import TipOptimizer
                snapshot = self.contract_invoker._gas_oracle.get_snapshot()
                optimizer = TipOptimizer(snapshot)
                p_fill = optimizer.p_fill.estimate(snapshot.tip_p50_gwei)
            except Exception as exc:
                logger.warning(
                    "C2: failed to derive p_fill from GasOracle (%s); "
                    "falling back to p_fill=1.0 (optimistic — verify gas oracle health).",
                    exc,
                )
                p_fill = 1.0

            optimal_size = self._calculate_optimal_size(opportunity)
            route = self._opportunity_to_route(opportunity)
            raw_spread = float(opportunity.sell_price - opportunity.buy_price)
            decision_plan = self.decide_contract_action(
                route,
                raw_spread=raw_spread,
                min_input=max(1.0, optimal_size * 0.5),
                max_input=max(2.0, optimal_size),
                gas_cost=max(1.0, opportunity.gas_estimate),
                pending_txs=[],
                steps=32,
                p_fill=p_fill,
            )
            return await self.execute_contract_decision(decision_plan)

        except Exception as exc:
            logger.error("C2 execute_arbitrage failed: %s", exc)
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
        p_fill: float = 1.0,
    ):
        """Sentinel -> decide duplicate/reverse/do nothing -> fork validate -> mempool validate.

        The ``profitability_gate`` (``P_net × P(fill) > 0``) is enforced for
        every action that involves execution, so that C2 only authorises a
        strike when fill probability is positive.  Pass ``p_fill`` from the
        live :class:`~apex_omega_core.core.mev_gas_oracle.TipOptimizer`; it
        defaults to ``1.0`` for backward compatibility.
        """
        pending = pending_txs or []
        sentinel_output = self.sentinel.build_c2_slippage_context(route, raw_spread, min_input, max_input, steps)
        total_slippage = sum(item['slippage'] for item in sentinel_output['slippage_per_leg'])
        net_profit = sentinel_output['profit'] - gas_cost

        reverse_route = self.sentinel.reverse_route(route)
        reverse_output = self.sentinel.optimize(reverse_route, min_input, max_input, steps=steps, raw_spread=-raw_spread)

        # Determine preliminary decision based on profitability metrics.
        gate_passed = profitability_gate(net_profit, p_fill)
        slippage_exceeded = total_slippage > self.max_total_slippage
        if not gate_passed or slippage_exceeded:
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
            # Glass-wall trace: exposes every profitability gate input and
            # result so the full decision chain is auditable.
            'gate_trace': {
                'p_net': net_profit,
                'p_fill': p_fill,
                'gate_passed': gate_passed,
                'total_slippage': total_slippage,
                'max_slippage_exceeded': slippage_exceeded,
            },
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

    def _opportunity_to_route(self, opportunity: ArbitrageOpportunity) -> List[Dict[str, Any]]:
        """Convert an :class:`ArbitrageOpportunity` into a sentinel route list.

        V3 (concentrated-liquidity) pools are supported via the virtual-reserve
        CPMM approximation: at the active tick, a V3 position is equivalent to
        a constant-product pool with ``reserve0 = L/sqrt_p`` and
        ``reserve1 = L*sqrt_p``.  See :mod:`apex_omega_core.core.v3_math`.

        Reserve resolution priority for each leg:

        1. V3 virtual reserves derived from ``pool.sqrt_price_x96`` +
           ``pool.liquidity`` (on-chain V3 state, decimal-normalised using
           ``pool.dec0`` / ``pool.dec1``).
        2. Pre-populated token-native reserves (``pool.reserve0`` /
           ``pool.reserve1`` > 0).  These may already be pre-computed
           virtual reserves for V3 pools.
        3. USD-TVL approximation (fallback, reduced accuracy, logged).
        """
        import math as _math
        from apex_omega_core.core.v3_math import v3_virtual_reserves

        def _resolve_reserves(pool) -> tuple:
            """Return (r0, r1) decimal-normalised reserves, or (None, None)."""
            if getattr(pool, "pool_type", "v2") == "v3":
                sqp = float(getattr(pool, "sqrt_price_x96", 0.0))
                liq = float(getattr(pool, "liquidity", 0.0))
                if sqp > 0 and liq > 0:
                    d0 = int(getattr(pool, "dec0", 18))
                    d1 = int(getattr(pool, "dec1", 18))
                    r0, r1 = v3_virtual_reserves(sqp, liq, dec0=d0, dec1=d1)
                    if r0 > 0 and r1 > 0:
                        return r0, r1
            if (
                pool.reserve0 > 0 and pool.reserve1 > 0
                and _math.isfinite(pool.reserve0) and _math.isfinite(pool.reserve1)
            ):
                return pool.reserve0, pool.reserve1
            return None, None

        # ── Buy leg (token1 → token0) ────────────────────────────────────────
        bp = opportunity.buy_pool
        _r0, _r1 = _resolve_reserves(bp)
        if _r0 is not None:
            buy_reserve_in = max(_r1, 1.0)
            buy_reserve_out = max(_r0, 1.0)
        else:
            logger.warning(
                "C2 route builder: buy_pool '%s' has no usable reserves; "
                "falling back to TVL approximation (reduced accuracy).",
                bp.address,
            )
            buy_reserve_in = max(bp.tvl_usd, 1.0)
            buy_reserve_out = max(bp.tvl_usd / max(opportunity.buy_price, 1e-9), 1.0)

        # ── Sell leg (token0 → token1) ───────────────────────────────────────
        sp = opportunity.sell_pool
        _r0, _r1 = _resolve_reserves(sp)
        if _r0 is not None:
            sell_reserve_in = max(_r0, 1.0)
            sell_reserve_out = max(_r1, 1.0)
        else:
            logger.warning(
                "C2 route builder: sell_pool '%s' has no usable reserves; "
                "falling back to TVL approximation (reduced accuracy).",
                sp.address,
            )
            sell_reserve_in = max(sp.tvl_usd / max(opportunity.sell_price, 1e-9), 1.0)
            sell_reserve_out = max(sp.tvl_usd, 1.0)

        return [
            {
                'venue': bp.dex,
                'pair': f"{bp.token1} → {bp.token0}",
                'reserve_in': buy_reserve_in,
                'reserve_out': buy_reserve_out,
                'fee': bp.fee,
                'price_in_usd': 1.0,
                'price_out_usd': max(opportunity.buy_price, 1e-9),
                'tvl_usd': max(bp.tvl_usd, 1.0),
                'volume_24h_usd': max(bp.tvl_usd * 0.5, 1.0),
                'age_in_blocks': _FRESHNESS_UNKNOWN_AGE_BLOCKS,
            },
            {
                'venue': sp.dex,
                'pair': f"{sp.token0} → {sp.token1}",
                'reserve_in': sell_reserve_in,
                'reserve_out': sell_reserve_out,
                'fee': sp.fee,
                'price_in_usd': max(opportunity.sell_price, 1e-9),
                'price_out_usd': 1.0,
                'tvl_usd': max(sp.tvl_usd, 1.0),
                'volume_24h_usd': max(sp.tvl_usd * 0.5, 1.0),
                'age_in_blocks': _FRESHNESS_UNKNOWN_AGE_BLOCKS,
            },
        ]

    # Legacy method for backward compatibility
    def execute(self, order: dict) -> ExecutionResult:
        """Legacy execution method"""
        slippage = Slippage(expected_price=order.get('price', 0), actual_price=order.get('price', 0), difference=0.0)
        return ExecutionResult(success=True, slippage=slippage)