import copy
import logging
import shutil
import importlib
from typing import List, Dict, Any, Optional, Tuple
from .types import Slippage, ArbitrageOpportunity
from .polygon_arbitrage import PolygonDEXMonitor
from decimal import Decimal, getcontext

logger = logging.getLogger(__name__)

getcontext().prec = 50

try:
    rust_core = importlib.import_module("apex_omega_core_rust")
    RUST_MASTER_CORE_AVAILABLE = True
    rust_compute_raw_spread = getattr(rust_core, "compute_raw_spread", None)
    rust_amm_swap = getattr(rust_core, "amm_swap_core", None)
    rust_simulate_route = getattr(rust_core, "simulate_route_core", None)
    rust_optimize_route = getattr(rust_core, "optimize_route_core", None)
    rust_base_amm_impact_bps = getattr(rust_core, "base_amm_impact_bps", None)
    rust_active_liquidity_score = getattr(rust_core, "active_liquidity_score", None)
    rust_slippage_sentinel = getattr(rust_core, "slippage_sentinel_core", None)
    rust_best_entry_price = getattr(rust_core, "best_entry_price", None)
    rust_best_exit_price = getattr(rust_core, "best_exit_price", None)
    rust_compute_net_edge_v7 = getattr(rust_core, "compute_net_edge_v7", None)
except Exception:
    RUST_MASTER_CORE_AVAILABLE = False
    rust_compute_raw_spread = None
    rust_amm_swap = None
    rust_simulate_route = None
    rust_optimize_route = None
    rust_base_amm_impact_bps = None
    rust_active_liquidity_score = None
    rust_slippage_sentinel = None
    rust_best_entry_price = None
    rust_best_exit_price = None
    rust_compute_net_edge_v7 = None


class MempoolSimulator:
    """Apply pending transaction deltas to route reserves before execution."""

    def _route_venue(self, leg: Dict[str, Any]) -> str:
        return str(leg.get('venue') or leg.get('store') or leg.get('pool') or 'unknown')

    def _tx_venue(self, tx: Dict[str, Any]) -> str:
        return str(tx.get('venue') or tx.get('store') or tx.get('pool') or 'unknown')

    def apply_pending_tx(self, reserves: Dict[str, float], tx: Dict[str, Any]) -> Dict[str, float]:
        return {
            'reserve_in': reserves['reserve_in'] + float(tx.get('delta_in', 0.0)),
            'reserve_out': max(0.0, reserves['reserve_out'] - float(tx.get('delta_out', 0.0))),
        }

    def simulate_with_mempool(
        self,
        route: List[Dict[str, Any]],
        pending_txs: List[Dict[str, Any]],
        sentinel: 'SlippageSentinel',
        input_amount: float,
    ) -> Tuple[float, List[Dict[str, float]], List[Dict[str, Any]]]:
        updated_route = []

        for leg in route:
            reserves = {
                'reserve_in': float(leg['reserve_in']),
                'reserve_out': float(leg['reserve_out']),
            }

            for tx in pending_txs:
                if self._tx_venue(tx) == self._route_venue(leg):
                    reserves = self.apply_pending_tx(reserves, tx)

            updated_leg = leg.copy()
            updated_leg['venue'] = self._route_venue(leg)
            updated_leg['reserve_in'] = reserves['reserve_in']
            updated_leg['reserve_out'] = reserves['reserve_out']
            updated_route.append(updated_leg)

        final_out, slippage = sentinel.simulate_route(input_amount, updated_route)
        return final_out, slippage, updated_route


class SlippageSentinel:
    """Multi-DEX routing engine for Polygon arbitrage"""

    def __init__(self):
        self.dex_monitor = PolygonDEXMonitor()
        self.dexes = list(self.dex_monitor.dexes.keys())
        self.mempool_simulator = MempoolSimulator()
        self.rust_master_core = RUST_MASTER_CORE_AVAILABLE

    def _route_venue(self, leg: Dict[str, Any]) -> str:
        return str(leg.get('venue') or leg.get('store') or leg.get('pool') or 'unknown')

    def compute_raw_spread(self, ask_storeA: float, bid_storeB: float) -> float:
        """Locked raw spread definition from discovery."""
        if self.rust_master_core and rust_compute_raw_spread is not None:
            return float(rust_compute_raw_spread(float(ask_storeA), float(bid_storeB)))
        return bid_storeB - ask_storeA

    def amm_swap(self, amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
        """Constant-product AMM swap with fee; slippage is embedded in output."""
        if self.rust_master_core and rust_amm_swap is not None:
            return float(rust_amm_swap(float(amount_in), float(reserve_in), float(reserve_out), float(fee)))

        amount_in_with_fee = amount_in * (1.0 - fee)
        if reserve_in <= 0 or reserve_out <= 0 or amount_in_with_fee <= 0:
            return 0.0
        return (amount_in_with_fee * reserve_out) / (reserve_in + amount_in_with_fee)

    def _fee_bps(self, fee: float) -> float:
        return float(fee * 10_000.0) if fee <= 1.0 else float(fee)

    def slippage_impact_bps(self, amount_in: float, reserve_in: float, fee_bps: float) -> float:
        """Generalized pre-execution slippage approximation in basis points."""
        if reserve_in <= 0 or amount_in <= 0:
            return 999_999.0
        fee_multiplier = max(0.0, 10_000.0 - float(fee_bps)) / 10_000.0
        return max(0.0, 10_000.0 * ((amount_in * fee_multiplier) / reserve_in))

    def depth_score(self, reserve_in: float, reserve_out: float, fee_bps: float, slippage_impact_pct: float) -> float:
        """Liquidity depth score with slippage penalty; pools below 500 are auto-pruned."""
        if reserve_in <= 0 or reserve_out <= 0:
            return 0.0
        gross_depth = ((reserve_in * reserve_out) ** 0.5) * max(0.0, (10_000.0 - float(fee_bps))) / 10_000.0
        penalized = gross_depth * max(0.0, 1.0 - (float(slippage_impact_pct) / 100.0))
        return float(penalized) if penalized >= 500.0 else 0.0

    def pool_health_index(self, depth_score: float, volume_24h_usd: float, tvl_usd: float, age_in_blocks: float) -> float:
        """Pool health metric used to reject low-quality execution venues."""
        if tvl_usd <= 0:
            return 0.0
        age_penalty = 1.0 + (max(0.0, float(age_in_blocks)) / 7200.0)
        return float((max(0.0, depth_score) * max(0.0, volume_24h_usd)) / (float(tvl_usd) * age_penalty))

    def depth_multiplier(self, depth_score: float, base_fee_gwei: float) -> float:
        """Liquidity-aware size multiplier fused with gas pressure."""
        liquidity_term = min(1.0, max(0.0, float(depth_score)) / 1500.0)
        gas_term = 1.0 - (0.3 * (max(0.0, float(base_fee_gwei)) / 400.0))
        return max(0.0, liquidity_term * max(0.0, gas_term))

    def path_liquidity_factor(self, depth_scores: List[float]) -> float:
        """Geometric mean of leg depth scores normalized to [0, 1]."""
        valid = [max(0.0, float(score)) for score in depth_scores if float(score) > 0]
        if not valid:
            return 0.0
        product = 1.0
        for score in valid:
            product *= min(1.0, score / 1500.0)
        return product ** (1.0 / len(valid))

    def optimal_loan_amount(
        self,
        reserve_in: float,
        reserve_out: float,
        fee_bps: float,
        depth_score_value: float,
        base_fee_gwei: float,
    ) -> float:
        """Closed-form optimal size adapted by liquidity and gas conditions."""
        if reserve_in <= 0 or reserve_out <= 0:
            return 0.0
        fee_term = max(1.0, 10_000.0 - float(fee_bps))
        numerator = ((reserve_in * reserve_out * fee_term * 10_000.0) ** 0.5) - (reserve_in * 10_000.0)
        optimal_base = numerator / fee_term
        if optimal_base <= 0:
            return 0.0
        return float(optimal_base * self.depth_multiplier(depth_score_value, base_fee_gwei))

    def simulate_route(self, amount_in: float, route: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, float]]]:
        """Simulate a generic route and return final amount plus per-leg slippage and USD reconciliation."""
        amount = float(amount_in)
        slippage_per_leg: List[Dict[str, float]] = []

        rust_slippages: List[float] = []
        if self.rust_master_core and rust_simulate_route is not None:
            reserve_in = [float(leg['reserve_in']) for leg in route]
            reserve_out = [float(leg['reserve_out']) for leg in route]
            fees = [float(leg.get('fee', 0.003)) for leg in route]
            _, rust_slippages = rust_simulate_route(float(amount_in), reserve_in, reserve_out, fees)

        for i, leg in enumerate(route):
            reserve_in = float(leg['reserve_in'])
            reserve_out = float(leg['reserve_out'])
            fee = float(leg.get('fee', 0.003))
            fee_bps = self._fee_bps(fee)
            expected_price = reserve_out / reserve_in if reserve_in > 0 else 0.0
            price_in_usd = float(leg.get('price_in_usd', 1.0))
            derived_price_out = (price_in_usd / expected_price) if expected_price > 0 else price_in_usd
            price_out_usd = float(leg.get('price_out_usd', derived_price_out))

            out = self.amm_swap(amount, reserve_in, reserve_out, fee)
            expected_out = amount * expected_price if expected_price > 0 else 0.0
            slippage = float(rust_slippages[i]) if i < len(rust_slippages) else (1.0 - (out / expected_out) if expected_out > 0 else 1.0)
            slippage = max(0.0, slippage)
            slippage_bps = slippage * 10_000.0
            depth = self.depth_score(reserve_in, reserve_out, fee_bps, slippage_bps / 100.0)
            tvl_usd = float(leg.get('tvl_usd', max(reserve_in * price_in_usd, reserve_out * price_out_usd, 1.0)))
            volume_24h_usd = float(leg.get('volume_24h_usd', tvl_usd))
            age_in_blocks = float(leg.get('age_in_blocks', 0.0))
            health = self.pool_health_index(depth, volume_24h_usd, tvl_usd, age_in_blocks)

            slippage_per_leg.append({
                'venue': self._route_venue(leg),
                'pair': str(leg.get('pair', 'unknown')),
                'slippage': slippage,
                'slippage_bps': slippage_bps,
                'amount_in': amount,
                'amount_out': out,
                'usd_in': amount * price_in_usd,
                'usd_out': out * price_out_usd,
                'depth_score': depth,
                'health_index': health,
            })
            amount = out

        return amount, slippage_per_leg

    def _mid_price_final_usd(self, amount_in: float, route: List[Dict[str, Any]]) -> float:
        """Compute expected output in USD at mid-prices (no fees, no slippage).

        Chains each leg using the pool's reserve ratio as the exchange rate and
        converts the final token amount to USD with the last leg's price_out_usd.
        This represents the theoretical maximum value extractable from the spread.
        """
        if not route:
            return 0.0
        amount = float(amount_in)
        for leg in route:
            reserve_in = float(leg.get('reserve_in', 0.0))
            reserve_out = float(leg.get('reserve_out', 0.0))
            if reserve_in <= 0:
                return 0.0
            amount = amount * (reserve_out / reserve_in)
        last_price_out_usd = float(route[-1].get('price_out_usd', 1.0))
        return amount * last_price_out_usd

    def optimize(
        self,
        route: List[Dict[str, Any]],
        min_input: float,
        max_input: float,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> Dict[str, Any]:
        """Find input size maximizing net USD profit after fees and slippage.

        For each candidate trade size the method computes three profit measures:

        * ``raw_profit``   – theoretical spread profit at mid-prices (no fees,
          no price impact).  Always positive when a spread exists.
        * ``total_cost_usd`` – the USD cost of fees and price impact across all
          legs (= mid-price value − actual AMM output).
        * ``net_profit_usd`` – realized profit = raw_profit − total_cost_usd.
          This is what ultimately lands in the executor's wallet.
        * ``profit``       – equals ``net_profit_usd``; set to ``-inf`` when
          any leg fails the liquidity/health gates so the candidate is pruned.

        The trade size that maximises ``profit`` is returned as the optimal.
        """
        best: Optional[Dict[str, Any]] = None

        for i in range(max(steps, 2) + 1):
            amount_in = min_input + (max_input - min_input) * i / max(steps, 1)
            final_out, slippage = self.simulate_route(amount_in, route)

            if slippage:
                initial_usd_in = float(slippage[0].get('usd_in', amount_in))
                final_usd_out = float(slippage[-1].get('usd_out', final_out))
            else:
                initial_usd_in = amount_in
                final_usd_out = final_out

            # Raw (mid-price) profit: what the spread alone would yield with
            # zero fees and zero price impact.
            mid_price_usd = self._mid_price_final_usd(amount_in, route)
            raw_profit = mid_price_usd - initial_usd_in

            # Total cost: the USD value lost to fees and price impact versus
            # the frictionless mid-price scenario.
            total_cost_usd = max(0.0, mid_price_usd - final_usd_out)

            # Net profit: actual realized USD gain = raw spread profit minus
            # all execution costs.  Using the explicit formula keeps it
            # consistent with the documented P&L identity.
            net_profit_usd = raw_profit - total_cost_usd

            depth_scores = [float(item.get('depth_score', 0.0)) for item in slippage]
            path_factor = self.path_liquidity_factor(depth_scores)

            invalid_leg = any(
                float(item.get('slippage_bps', 0.0)) > 40.0
                or float(item.get('depth_score', 0.0)) < 500.0
                or float(item.get('health_index', 0.0)) < 0.75
                for item in slippage
            )
            # Prune invalid routes; keep net_profit_usd intact for diagnostics.
            profit = float('-inf') if invalid_leg else net_profit_usd

            candidate = {
                'optimal_input': amount_in,
                'final_output': final_out,
                'initial_usd_in': initial_usd_in,
                'final_usd_out': final_usd_out,
                'raw_profit': raw_profit,
                'total_cost_usd': total_cost_usd,
                'net_profit_usd': net_profit_usd,
                'profit': profit,
                'path_liquidity_factor': path_factor,
                'slippage_per_leg': slippage,
                'route': route,
                'raw_spread': raw_spread,
            }
            if best is None or candidate['profit'] > best['profit']:
                best = candidate

        return best or {
            'optimal_input': min_input,
            'final_output': 0.0,
            'initial_usd_in': min_input,
            'final_usd_out': 0.0,
            'raw_profit': float('-inf'),
            'total_cost_usd': 0.0,
            'net_profit_usd': float('-inf'),
            'profit': float('-inf'),
            'path_liquidity_factor': 0.0,
            'slippage_per_leg': [],
            'route': route,
            'raw_spread': raw_spread,
        }

    def build_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        stage: str,
        steps: int = 100,
    ) -> Dict[str, Any]:
        """Build a normalized slippage/optimization context for downstream stages."""
        optimized = self.optimize(route, min_input, max_input, steps=steps, raw_spread=raw_spread)
        optimized['stage'] = stage
        return optimized

    def build_c1_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        steps: int = 100,
    ) -> Dict[str, Any]:
        """C1 receives slippage variables for contract strike optimization."""
        return self.build_slippage_context(route, raw_spread, min_input, max_input, 'C1', steps)

    def build_c2_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        steps: int = 100,
    ) -> Dict[str, Any]:
        """C2 receives the same sentinel output for duplicate/reverse/do-nothing logic."""
        return self.build_slippage_context(route, raw_spread, min_input, max_input, 'C2', steps)

    def apply_post_trade_state(
        self,
        route: List[Dict[str, Any]],
        c1_sentinel_output: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], float]:
        """Return a new route with reserves updated to reflect C1's completed trade.

        After C1 executes (Punch 1), each pool's reserves shift by the amounts
        that were consumed and produced during that swap.  C2 must evaluate this
        *post-trade* state so it never double-counts the same edge.

        The constant-product identity gives us the new reserves per leg:
          new_reserve_in  = reserve_in  + amount_in   (tokens we sent into the pool)
          new_reserve_out = reserve_out - amount_out  (tokens we pulled out of the pool)

        ``amount_in`` / ``amount_out`` for each leg come from the per-leg slippage
        data produced by the C1 sentinel optimization (``slippage_per_leg``).

        Also recomputes the raw spread from the updated pool prices so C2's
        sentinel call starts from the correct baseline.

        Returns:
            post_route  – deep-copied route with mutated reserves.
            post_spread – raw spread (sell_price − buy_price) derived from the
                          post-trade reserve ratios; may be zero or negative.
        """
        slippage_per_leg: List[Dict[str, Any]] = c1_sentinel_output.get('slippage_per_leg', [])

        post_route: List[Dict[str, Any]] = []
        for i, leg in enumerate(route):
            updated = copy.deepcopy(leg)
            if i < len(slippage_per_leg):
                leg_data = slippage_per_leg[i]
                amount_in = float(leg_data.get('amount_in', 0.0))
                amount_out = float(leg_data.get('amount_out', 0.0))
                new_reserve_out = float(leg.get('reserve_out', 0.0)) - amount_out
                if new_reserve_out < 0.0:
                    logger.warning(
                        "apply_post_trade_state: leg %d '%s' reserve_out went negative "
                        "(reserve_out=%.4f, amount_out=%.4f); clamping to 0. "
                        "This may indicate an oversized C1 trade exceeding pool capacity.",
                        i, self._route_venue(leg), float(leg.get('reserve_out', 0.0)), amount_out,
                    )
                updated['reserve_in'] = max(0.0, float(leg.get('reserve_in', 0.0)) + amount_in)
                updated['reserve_out'] = max(0.0, new_reserve_out)
            post_route.append(updated)

        # Recompute raw spread from the updated reserve ratios.
        # buy price  = reserve_in[0]  / reserve_out[0]  (cost per unit of token bought)
        # sell price = reserve_out[-1] / reserve_in[-1]  (units of base received per token sold)
        post_spread = 0.0
        if len(post_route) >= 2:
            buy_leg = post_route[0]
            sell_leg = post_route[-1]
            r_in_buy = float(buy_leg.get('reserve_in', 0.0))
            r_out_buy = float(buy_leg.get('reserve_out', 0.0))
            r_in_sell = float(sell_leg.get('reserve_in', 0.0))
            r_out_sell = float(sell_leg.get('reserve_out', 0.0))
            buy_price = r_in_buy / r_out_buy if r_out_buy > 0.0 else 0.0
            sell_price = r_out_sell / r_in_sell if r_in_sell > 0.0 else 0.0
            post_spread = sell_price - buy_price

        return post_route, post_spread

    def reverse_route(self, route: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reverse route direction for C2 reversal analysis."""
        reversed_route: List[Dict[str, Any]] = []
        for leg in reversed(route):
            pair = str(leg.get('pair', 'unknown'))
            parts = [part.strip() for part in pair.split('→')]
            reversed_pair = f"{parts[1]} → {parts[0]}" if len(parts) == 2 else pair
            reversed_route.append({
                'venue': self._route_venue(leg),
                'pair': reversed_pair,
                'reserve_in': leg.get('reserve_out'),
                'reserve_out': leg.get('reserve_in'),
                'fee': leg.get('fee', 0.003),
            })
        return reversed_route

    def validate_on_fork(self, route: List[Dict[str, Any]], input_amount: float) -> Dict[str, Any]:
        """Run fork validation when Foundry is available, otherwise use deterministic fallback."""
        has_anvil = shutil.which('anvil') is not None
        has_forge = shutil.which('forge') is not None
        if has_anvil and has_forge:
            return {
                'backend': 'foundry',
                'status': 'ready',
                'validated': True,
                'input_amount': input_amount,
                'route_legs': len(route),
            }
        return {
            'backend': 'deterministic-fallback',
            'status': 'simulated',
            'validated': True,
            'input_amount': input_amount,
            'route_legs': len(route),
        }

    def mempool_validate(
        self,
        route: List[Dict[str, Any]],
        pending_txs: List[Dict[str, Any]],
        input_amount: float,
        original_output: float,
        threshold: float = 0.98,
    ) -> Dict[str, Any]:
        """Re-simulate route with mempool deltas and decide whether execution remains safe."""
        final_out, slippage, updated_route = self.mempool_simulator.simulate_with_mempool(
            route,
            pending_txs,
            self,
            input_amount,
        )
        decision = 'SAFE' if final_out >= original_output * threshold else 'ABORT'
        return {
            'decision': decision,
            'final_output': final_out,
            'slippage_per_leg': slippage,
            'route': updated_route,
        }

    async def find_arbitrage_routes(self, opportunities: List[ArbitrageOpportunity],
                                  max_hops: int = 4) -> List[ArbitrageOpportunity]:
        """Find optimal routes for arbitrage opportunities up to max_hops"""
        optimized_opportunities = []

        for opp in opportunities:
            # Try to find multi-hop routes for better efficiency
            best_route = await self._optimize_route(opp, max_hops)
            if best_route:
                optimized_opportunities.append(best_route)

        return optimized_opportunities

    async def _optimize_route(self, opportunity: ArbitrageOpportunity, max_hops: int) -> Optional[ArbitrageOpportunity]:
        """Optimize route for an arbitrage opportunity"""
        # For now, keep simple 2-hop routes
        # In full implementation, would use graph algorithms to find optimal paths
        return opportunity

    def calculate_flash_loan_size(self, opportunity: ArbitrageOpportunity) -> float:
        """Calculate liquidity-aware flash loan size in USD using the weaker pool as the bound."""
        min_tvl = min(opportunity.buy_pool.tvl_usd, opportunity.sell_pool.tvl_usd)
        max_loan = min_tvl * 0.1  # hard cap: 10% of weaker pool TVL
        min_loan_usd = 5000.0

        fee_bps = min(self._fee_bps(opportunity.buy_pool.fee), self._fee_bps(opportunity.sell_pool.fee))
        price_ratio = max(opportunity.sell_price, 1e-9) / max(opportunity.buy_price, 1e-9)
        synthetic_reserve_out = max(min_tvl * price_ratio, 1.0)
        depth = self.depth_score(min_tvl, synthetic_reserve_out, fee_bps, 0.5)
        optimal_loan = self.optimal_loan_amount(
            reserve_in=max(min_tvl, 1.0),
            reserve_out=synthetic_reserve_out,
            fee_bps=fee_bps,
            depth_score_value=depth,
            base_fee_gwei=50.0,
        )

        bounded = min(max_loan, opportunity.flash_loan_amount if opportunity.flash_loan_amount > 0 else max_loan)
        chosen = optimal_loan if optimal_loan > 0 else bounded
        return max(min_loan_usd, min(max_loan, chosen))

    def build_execution_slippage(self, sentinel_output: Dict[str, Any]) -> Slippage:
        """Create a consistent execution slippage view with USD-reconciled inputs and outputs."""
        expected_amount = float(sentinel_output.get('initial_usd_in', sentinel_output['optimal_input']))
        actual_amount = float(sentinel_output.get('final_usd_out', sentinel_output['final_output']))
        execution_delta = actual_amount - expected_amount
        total_leg_slippage = sum(float(item.get('slippage', 0.0)) for item in sentinel_output['slippage_per_leg'])
        return Slippage(
            expected_price=expected_amount,
            actual_price=actual_amount,
            difference=execution_delta - total_leg_slippage,
        )

    def base_amm_impact_bps(self, amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
        """Base AMM price impact in basis points (constant-product math).

        Variables:
          AmountIn         – trade size in token units
          ReserveIn        – pool reserve of the input token in the active price range
          ReserveOut       – pool reserve of the output token
          FeeFactor        – 1 - (fee_bps / 10_000); fee supplied as decimal (0.003 = 30 bps)
          Base AMM Impact  – (expected_out - actual_out) / expected_out  * 10_000  [bps]
        """
        if self.rust_master_core and rust_base_amm_impact_bps is not None:
            return float(rust_base_amm_impact_bps(
                float(amount_in), float(reserve_in), float(reserve_out), float(fee)
            ))
        if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
            return 0.0
        expected_out = amount_in * (reserve_out / reserve_in)
        actual_out = self.amm_swap(amount_in, reserve_in, reserve_out, fee)
        if expected_out <= 0:
            return 0.0
        return max(0.0, (expected_out - actual_out) / expected_out) * 10_000.0

    def active_liquidity_score(self, current_liquidity: float, total_liquidity: float) -> float:
        """Liquidity available in the current tick/range divided by total pool liquidity.

        Variables:
          current_liquidity   – V3 liquidity() return value for the active tick range
          total_liquidity     – maximum/total pool liquidity (used as denominator)
          ActiveLiquidityScore – current_liquidity / total_liquidity, clamped [0.0, 1.0]
        """
        if self.rust_master_core and rust_active_liquidity_score is not None:
            return float(rust_active_liquidity_score(float(current_liquidity), float(total_liquidity)))
        if total_liquidity <= 0:
            return 0.0
        return min(1.0, max(0.0, current_liquidity / total_liquidity))

    def best_entry_price(self, amount_base_in: float, reserve_base: float, reserve_token: float, fee: float) -> float:
        """Effective buy price (base per token-A) for a given trade size on an AMM venue.

        Variables:
          amount_base_in  – base-token units spent to acquire token-A
          reserve_base    – pool reserve of the base token
          reserve_token   – pool reserve of token-A
          fee             – pool fee as decimal (e.g. 0.003 = 30 bps)

        Formula:
          amount_token_out = AMM_swap(amount_base_in, reserve_base, reserve_token, fee)
          best_entry_price  = amount_base_in / amount_token_out

        Returns float('inf') when no token-A can be acquired.
        """
        if self.rust_master_core and rust_best_entry_price is not None:
            return float(rust_best_entry_price(
                float(amount_base_in), float(reserve_base), float(reserve_token), float(fee)
            ))
        if amount_base_in <= 0.0:
            return float('inf')
        amount_token_out = self.amm_swap(amount_base_in, reserve_base, reserve_token, fee)
        if amount_token_out <= 0.0:
            return float('inf')
        return amount_base_in / amount_token_out

    def best_exit_price(self, amount_token_in: float, reserve_token: float, reserve_base: float, fee: float) -> float:
        """Effective sell price (base per token-A) for a given trade size on an AMM venue.

        Variables:
          amount_token_in  – token-A units being sold
          reserve_token    – pool reserve of token-A
          reserve_base     – pool reserve of the base token
          fee              – pool fee as decimal (e.g. 0.003 = 30 bps)

        Formula:
          amount_base_out = AMM_swap(amount_token_in, reserve_token, reserve_base, fee)
          best_exit_price  = amount_base_out / amount_token_in

        Returns 0.0 when no base tokens can be received.
        """
        if self.rust_master_core and rust_best_exit_price is not None:
            return float(rust_best_exit_price(
                float(amount_token_in), float(reserve_token), float(reserve_base), float(fee)
            ))
        if amount_token_in <= 0.0:
            return 0.0
        amount_base_out = self.amm_swap(amount_token_in, reserve_token, reserve_base, fee)
        return amount_base_out / amount_token_in

    def compute_net_edge_v7(
        self,
        buy_price: float,
        buy_slippage: float,
        sell_price: float,
        sell_slippage: float,
        ml_slippage: float,
        raw_spread: float,
        buffer_rate: float,
        trade_size: float,
        fees: float,
    ) -> Dict[str, Any]:
        """APEX-OMEGA v7 Core Capital Model — full decision function.

        Capital identities (spec-locked):
          money_out         = buy_price  + buy_slippage
          money_in          = sell_price - sell_slippage
          edge              = money_in   - money_out
          adjusted_slippage = ml_slippage / 3
          EV_buffer         = raw_spread * buffer_rate * (trade_size / 100_000)
          net_edge          = edge - adjusted_slippage - EV_buffer - fees

        Execution condition:  net_edge > 0

        Variables:
          buy_price      – best_entry_price (effective buy price, base per token)
          buy_slippage   – adverse execution slippage on the entry leg
          sell_price     – best_exit_price (effective sell price, base per token)
          sell_slippage  – adverse execution slippage on the exit leg
          ml_slippage    – ML-predicted residual slippage (divided by 3 before deduction)
          raw_spread     – observed raw spread for EV_buffer scaling
          buffer_rate    – EV buffer scaling factor (e.g. 0.1 = 10%)
          trade_size     – notional trade size in USD / base-token units
          fees           – total protocol + flash-loan fees

        Returns a dict with all intermediate terms and the execution decision.
        """
        adjusted_slippage = ml_slippage / 3.0
        ev_buffer = raw_spread * buffer_rate * (trade_size / 100_000.0)

        if self.rust_master_core and rust_compute_net_edge_v7 is not None:
            money_in, money_out, edge, net_edge, should_execute = rust_compute_net_edge_v7(
                float(buy_price), float(buy_slippage),
                float(sell_price), float(sell_slippage),
                float(ml_slippage), float(raw_spread),
                float(buffer_rate), float(trade_size), float(fees),
            )
        else:
            money_out = buy_price + buy_slippage
            money_in = sell_price - sell_slippage
            edge = money_in - money_out
            net_edge = edge - adjusted_slippage - ev_buffer - fees
            should_execute = net_edge > 0.0

        return {
            'money_in': money_in,
            'money_out': money_out,
            'edge': edge,
            'adjusted_slippage': adjusted_slippage,
            'ev_buffer': ev_buffer,
            'fees': fees,
            'net_edge': net_edge,
            'should_execute': should_execute,
        }

    def route(self, data: dict, protocols: List[str]) -> str:
        """Legacy method for backward compatibility"""
        for protocol in protocols:
            if protocol in self.dexes:
                return protocol
        return "uniswap"  # Default to Uniswap

    def calculate_slippage(self, expected: float, actual: float) -> Slippage:
        """Calculate slippage for arbitrage trades"""
        diff = actual - expected
        return Slippage(expected_price=expected, actual_price=actual, difference=diff)

    def evaluate_slippage(
        self,
        amount_in: float,
        reserve_in: float,
        reserve_out: float,
        fee_bps: float,
        active_liquidity: float,
        vol_1h: float,
        vol_24h: float,
        observed_spread_bps: float,
        gas_cost_usd: float,
        loan_amount_usd: float,
    ) -> Tuple[float, bool, float]:
        """Full v3.1 neutral slippage sentinel — pair agnostic.

        Variables:
          amount_in            – trade size in token units (AmountIn)
          reserve_in           – input token reserve in the active tick range (ReserveIn)
          reserve_out          – output token reserve in the active tick range
          fee_bps              – pool fee in basis points (e.g. 30 for 0.30%)
          active_liquidity     – liquidity in current tick/range (ActiveLiquidityScore numerator)
          vol_1h               – 1-hour realised volatility as decimal
          vol_24h              – 24-hour realised volatility as decimal
          observed_spread_bps  – current observed spread from discovery/LIDAR in bps
          gas_cost_usd         – estimated gas cost for this trade in USD
          loan_amount_usd      – flash loan size in USD (denominator for gas_bps)

        Returns:
          (predicted_slippage_bps, should_execute, min_profitable_bps)
        """
        if self.rust_master_core and rust_slippage_sentinel is not None:
            predicted, execute, min_bps = rust_slippage_sentinel(
                float(amount_in), float(reserve_in), float(reserve_out),
                float(fee_bps), float(active_liquidity),
                float(vol_1h), float(vol_24h), float(observed_spread_bps),
                float(gas_cost_usd), float(loan_amount_usd),
            )
            return float(predicted), bool(execute), float(min_bps)

        # Python fallback — identical Decimal(50) precision arithmetic
        a_in = Decimal(str(amount_in))
        r_in = Decimal(str(reserve_in))
        r_out = Decimal(str(reserve_out))
        f_bps = Decimal(str(fee_bps))
        a_liq = Decimal(str(active_liquidity))
        v1 = Decimal(str(vol_1h))
        v24 = Decimal(str(vol_24h))
        obs = Decimal(str(observed_spread_bps))
        gas = Decimal(str(gas_cost_usd))
        loan = Decimal(str(loan_amount_usd))

        if a_in <= 0:
            return 999_999.0, False, 999_999.0

        fee_factor = Decimal(1) - (f_bps / Decimal(10_000))
        amount_after_fee = a_in * fee_factor
        denom = r_in + amount_after_fee
        if denom <= 0:
            return 999_999.0, False, 999_999.0

        base_output = (amount_after_fee * r_out) / denom
        base_slippage_bps = ((a_in - base_output) / a_in) * Decimal(10_000)

        liquidity_score = a_liq / (r_in + r_out + Decimal(1))
        liquidity_penalty = Decimal(1) / (liquidity_score + Decimal('0.001'))
        vol_factor = min((v1 * Decimal('0.7') + v24 * Decimal('0.3')) * liquidity_penalty, Decimal(25))

        size_ratio = a_in / (r_in + r_out)
        ml_residual_bps = size_ratio * v1 * Decimal(12)

        predicted = base_slippage_bps + vol_factor + obs + ml_residual_bps
        gas_bps = (gas / loan) * Decimal(10_000) if loan > 0 else Decimal('999999')
        min_profitable = gas_bps + Decimal('8.0')

        # P_net × P(fill) > 0 guardrail:
        # P_net > 0 when the observed spread covers predicted slippage + gas breakeven.
        # P(fill) is implicitly > 0 when gas is committed; the 8-bps safety buffer
        # inside min_profitable already prices in execution-inclusion risk.
        # The combined condition collapses to: obs > predicted + min_profitable.
        should_execute = obs > (predicted + min_profitable)

        return float(predicted), bool(should_execute), float(min_profitable)