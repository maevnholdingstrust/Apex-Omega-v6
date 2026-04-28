import logging
from typing import Any, Dict, List

from apex_omega_core.core.types import ExecutionResult, Slippage, ArbitrageOpportunity
from apex_omega_core.core.contract_targets import C1_TARGET
from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.inference import profitability_gate

logger = logging.getLogger(__name__)

# Sentinel value used for age_in_blocks when the pool snapshot age is unknown.
# Zero signals "no stale-data penalty" rather than misrepresenting the data as
# 120 blocks old (≈ 4 min on Polygon).
_FRESHNESS_UNKNOWN_AGE_BLOCKS: float = 0.0

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
        p_fill: float = 1.0,
    ):
        """Discovery -> sentinel optimize -> fork validate -> mempool validate for C1.

        The ``profitability_gate`` (``P_net × P(fill) > 0``) is enforced on
        the strike decision so that execution is only triggered when both net
        profit and fill probability are positive.  Pass ``p_fill`` from the
        live :class:`~apex_omega_core.core.mev_gas_oracle.TipOptimizer` to
        incorporate real inclusion probability; defaults to ``1.0`` for
        backward compatibility.
        """
        pending = pending_txs or []
        sentinel_output = self.sentinel.build_c1_slippage_context(route, raw_spread, min_input, max_input, steps)
        fork_validation = self.sentinel.validate_on_fork(route, sentinel_output['optimal_input'])
        mempool_validation = self.sentinel.mempool_validate(
            route,
            pending,
            sentinel_output['optimal_input'],
            sentinel_output['final_output'],
        )
        # Enforce the P_net × P(fill) > 0 gate on every strike path.
        net_edge = float(sentinel_output.get('profit', 0.0))
        gate_passed = profitability_gate(net_edge, p_fill)
        should_strike = (
            gate_passed
            and mempool_validation['decision'] == 'SAFE'
        )
        return {
            'sentinel_output': sentinel_output,
            'fork_validation': fork_validation,
            'mempool_validation': mempool_validation,
            'target_address': self.target_address,
            'action': 'STRIKE' if should_strike else 'ABORT',
            'p_fill': p_fill,
            # Glass-wall trace: every caller can inspect the profitability gate
            # inputs and result without re-deriving them.
            'gate_trace': {
                'p_net': net_edge,
                'p_fill': p_fill,
                'gate_passed': gate_passed,
            },
        }

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> ExecutionResult:
        """Execute arbitrage opportunity with maximum speed using flash loans.

        Derives live P(fill) from the gas oracle, builds a sentinel strike plan,
        and delegates to :meth:`execute_contract_strike` so the real on-chain
        tx hash is propagated back to the caller rather than a hardcoded address.
        """
        try:
            if opportunity.estimated_profit_usd < self.min_expected_profit_usd:
                return ExecutionResult(success=False)

            try:
                from apex_omega_core.core.mev_gas_oracle import TipOptimizer
                snapshot = self.contract_invoker._gas_oracle.get_snapshot()
                optimizer = TipOptimizer(snapshot)
                p_fill = optimizer.p_fill.estimate(snapshot.tip_p50_gwei)
            except Exception as exc:
                logger.warning(
                    "C1: failed to derive p_fill from GasOracle (%s); "
                    "falling back to p_fill=1.0 (optimistic — verify gas oracle health).",
                    exc,
                )
                p_fill = 1.0

            route = self._opportunity_to_route(opportunity)
            raw_spread = float(opportunity.sell_price - opportunity.buy_price)
            strike_plan = self.prepare_contract_strike(
                route,
                raw_spread=raw_spread,
                min_input=max(1.0, opportunity.flash_loan_amount * 0.5),
                max_input=max(2.0, opportunity.flash_loan_amount),
                pending_txs=[],
                steps=32,
                p_fill=p_fill,
            )
            return await self.execute_contract_strike(strike_plan)

        except Exception as exc:
            logger.error("C1 execute_arbitrage failed: %s", exc)
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
                "C1 route builder: buy_pool '%s' has no usable reserves; "
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
                "C1 route builder: sell_pool '%s' has no usable reserves; "
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
        slippage = Slippage(expected_price=order.get('price', 0), actual_price=order.get('price', 0) + 0.01, difference=0.01)
        return ExecutionResult(success=True, slippage=slippage)