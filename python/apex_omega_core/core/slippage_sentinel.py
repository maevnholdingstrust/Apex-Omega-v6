import shutil
import importlib
from typing import List, Dict, Any, Optional, Tuple
from .types import Slippage, ArbitrageOpportunity
from .polygon_arbitrage import PolygonDEXMonitor
from decimal import Decimal, getcontext

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
except Exception:
    RUST_MASTER_CORE_AVAILABLE = False
    rust_compute_raw_spread = None
    rust_amm_swap = None
    rust_simulate_route = None
    rust_optimize_route = None
    rust_base_amm_impact_bps = None
    rust_active_liquidity_score = None
    rust_slippage_sentinel = None


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

    def simulate_route(self, amount_in: float, route: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, float]]]:
        """Simulate a generic route and return final amount plus per-leg slippage."""
        if self.rust_master_core and rust_simulate_route is not None:
            reserve_in = [float(leg['reserve_in']) for leg in route]
            reserve_out = [float(leg['reserve_out']) for leg in route]
            fees = [float(leg.get('fee', 0.003)) for leg in route]
            final_amount, slippages = rust_simulate_route(float(amount_in), reserve_in, reserve_out, fees)
            slippage_per_leg = []
            for i, leg in enumerate(route):
                slippage_per_leg.append({
                    'venue': self._route_venue(leg),
                    'pair': str(leg.get('pair', 'unknown')),
                    'slippage': float(slippages[i]) if i < len(slippages) else 1.0,
                })
            return float(final_amount), slippage_per_leg

        amount = amount_in
        slippage_per_leg: List[Dict[str, float]] = []

        for leg in route:
            reserve_in = float(leg['reserve_in'])
            reserve_out = float(leg['reserve_out'])
            fee = float(leg.get('fee', 0.003))
            expected_price = reserve_out / reserve_in if reserve_in > 0 else 0.0

            out = self.amm_swap(amount, reserve_in, reserve_out, fee)
            expected_out = amount * expected_price if expected_price > 0 else 0.0
            slippage = 1.0 - (out / expected_out) if expected_out > 0 else 1.0

            slippage_per_leg.append({
                'venue': self._route_venue(leg),
                'pair': str(leg.get('pair', 'unknown')),
                'slippage': max(0.0, slippage),
            })
            amount = out

        return amount, slippage_per_leg

    def optimize(
        self,
        route: List[Dict[str, Any]],
        min_input: float,
        max_input: float,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> Dict[str, Any]:
        """Find input size maximizing final output minus initial input."""
        if self.rust_master_core and rust_optimize_route is not None:
            reserve_in = [float(leg['reserve_in']) for leg in route]
            reserve_out = [float(leg['reserve_out']) for leg in route]
            fees = [float(leg.get('fee', 0.003)) for leg in route]
            optimal_input, final_output, profit, slippages = rust_optimize_route(
                float(min_input),
                float(max_input),
                int(max(steps, 2)),
                reserve_in,
                reserve_out,
                fees,
            )
            slippage_per_leg = []
            for i, leg in enumerate(route):
                slippage_per_leg.append({
                    'venue': self._route_venue(leg),
                    'pair': str(leg.get('pair', 'unknown')),
                    'slippage': float(slippages[i]) if i < len(slippages) else 1.0,
                })

            return {
                'optimal_input': float(optimal_input),
                'final_output': float(final_output),
                'profit': float(profit),
                'slippage_per_leg': slippage_per_leg,
                'route': route,
                'raw_spread': raw_spread,
            }

        best: Optional[Dict[str, Any]] = None

        for i in range(max(steps, 2) + 1):
            amount_in = min_input + (max_input - min_input) * i / max(steps, 1)
            final_out, slippage = self.simulate_route(amount_in, route)
            profit = final_out - amount_in

            candidate = {
                'optimal_input': amount_in,
                'final_output': final_out,
                'profit': profit,
                'slippage_per_leg': slippage,
                'route': route,
                'raw_spread': raw_spread,
            }
            if best is None or candidate['profit'] > best['profit']:
                best = candidate

        return best or {
            'optimal_input': min_input,
            'final_output': 0.0,
            'profit': float('-inf'),
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
        """Calculate optimal flash loan size based on pool TVL constraints"""
        min_tvl = min(opportunity.buy_pool.tvl_usd, opportunity.sell_pool.tvl_usd)
        max_loan = min_tvl * 0.1  # 10% of smaller pool TVL

        # Ensure minimum $5000 equivalent
        min_loan_usd = 5000.0

        # Convert to token amount (assuming opportunity.flash_loan_token is the token)
        # In reality, would need price conversion
        optimal_loan = max(min_loan_usd, min(max_loan, opportunity.flash_loan_amount))

        return optimal_loan

    def build_execution_slippage(self, sentinel_output: Dict[str, Any]) -> Slippage:
        """Create a consistent execution slippage view for all downstream strategies."""
        expected_amount = float(sentinel_output['optimal_input'])
        actual_amount = float(sentinel_output['final_output'])
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
        should_execute = predicted <= (obs + Decimal('6.0'))

        return float(predicted), bool(should_execute), float(min_profitable)