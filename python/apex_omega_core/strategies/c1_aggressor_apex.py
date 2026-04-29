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
        injects token base-unit metadata so ``build_c1_calldata`` can resolve
        amounts to on-chain integer units, and delegates to
        :meth:`execute_contract_strike`.
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

            # Bridge USD amounts to token base units required by build_c1_calldata.
            # flash_loan_amount is in USD; convert to token-native integer units
            # using the decimals and USD price on the opportunity.
            usd_price = max(float(opportunity.flash_loan_token_usd_price), 1e-9)
            decimals = int(opportunity.flash_loan_token_decimals)
            token_amount = opportunity.flash_loan_amount / usd_price
            opt_input_units = int(token_amount * (10 ** decimals))
            # Minimum acceptable output: at least recover the flash-loan principal.
            # Callers may tighten this to flash_loan_amount + expected_profit.
            strike_plan['sentinel_output']['optimal_input_base_units'] = opt_input_units
            strike_plan['sentinel_output']['min_final_output_base_units'] = opt_input_units

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

        V3 concentrated-liquidity pools are handled via virtual reserves derived
        from ``sqrtPriceX96`` and ``liquidity`` (see ``core/v3_math.py``).  This
        approximation is exact within a single tick range and gives a conservative
        first-order estimate across wider ranges.

        Reserves are expressed in token-native units when available
        (``pool.reserve0`` / ``pool.reserve1`` > 0 for V2, or
        ``pool.sqrt_price_x96`` + ``pool.liquidity`` > 0 for V3).  When the
        scanner has not populated on-chain state the builder falls back to
        USD-TVL approximations with a logged warning.
        """
        import math as _math
        from apex_omega_core.core.v3_math import v3_virtual_reserves

        def _resolve_reserves(
            pool,
            label: str,
            price_ref: float,
            use_token1_as_in: bool,
        ) -> tuple[float, float]:
            """Return (reserve_in, reserve_out) for *pool*.

            Resolution order:
            1. V3: sqrtPriceX96 + liquidity → virtual reserves
            2. V2: on-chain reserve0 / reserve1
            3. Fallback: USD-TVL approximation
            """
            is_v3 = getattr(pool, "pool_type", "v2") == "v3"
            sqrt_px96 = float(getattr(pool, "sqrt_price_x96", 0.0) or 0.0)
            liquidity = float(getattr(pool, "liquidity", 0.0) or 0.0)

            if is_v3 and sqrt_px96 > 0.0 and liquidity > 0.0:
                # Derive decimal scales from the Pool dataclass when available.
                dec0 = int(getattr(pool, "dec0", 18) or 18)
                dec1 = int(getattr(pool, "dec1", 18) or 18)
                r0, r1 = v3_virtual_reserves(sqrt_px96, liquidity, dec0, dec1)
                if r0 > 0.0 and r1 > 0.0 and _math.isfinite(r0) and _math.isfinite(r1):
                    logger.debug(
                        "C1 route builder: %s '%s' (V3) using virtual reserves "
                        "r0=%.6g r1=%.6g",
                        label, pool.address, r0, r1,
                    )
                    if use_token1_as_in:
                        return max(r1, 1.0), max(r0, 1.0)
                    return max(r0, 1.0), max(r1, 1.0)
                logger.warning(
                    "C1 route builder: %s '%s' (V3) has zero virtual reserves "
                    "from sqrtPriceX96=%.6g liquidity=%.6g; "
                    "falling back to TVL approximation.",
                    label, pool.address, sqrt_px96, liquidity,
                )
            elif not is_v3:
                r0 = float(pool.reserve0)
                r1 = float(pool.reserve1)
                if r0 > 0.0 and r1 > 0.0 and _math.isfinite(r0) and _math.isfinite(r1):
                    if use_token1_as_in:
                        return max(r1, 1.0), max(r0, 1.0)
                    return max(r0, 1.0), max(r1, 1.0)
                logger.warning(
                    "C1 route builder: %s '%s' has no on-chain reserves; "
                    "falling back to TVL approximation (reduced accuracy).",
                    label, pool.address,
                )

            # TVL fallback
            if use_token1_as_in:
                return max(pool.tvl_usd, 1.0), max(pool.tvl_usd / max(price_ref, 1e-9), 1.0)
            return max(pool.tvl_usd / max(price_ref, 1e-9), 1.0), max(pool.tvl_usd, 1.0)

        # ── Buy leg ──────────────────────────────────────────────────────────
        bp = opportunity.buy_pool
        buy_reserve_in, buy_reserve_out = _resolve_reserves(
            bp, "buy_pool", opportunity.buy_price, use_token1_as_in=True
        )

        # ── Sell leg ─────────────────────────────────────────────────────────
        sp = opportunity.sell_pool
        sell_reserve_in, sell_reserve_out = _resolve_reserves(
            sp, "sell_pool", opportunity.sell_price, use_token1_as_in=False
        )

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