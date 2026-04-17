"""
EV Engine — merged C1 (math/optimization) + C2 (decision/probability) pipeline.

Given a route, returns ONLY executable, EV-positive trades:

    EV = net_profit * p_exec - (1 - p_exec) * failure_cost > 0

Pipeline:
    1. Simulate raw AMM output
    2. Apply reality buffer (slippage_base + mempool_drift + competition_pressure)
    3. Optimize trade size (non-linear grid search)
    4. Gas-integrated net profit
    5. Execution probability model (p_exec)
    6. Expected-value calculation
    7. Hard filters (EV > 0, net_profit > MIN_PROFIT, p_exec >= MIN_CONFIDENCE)
    8. Return ExecutableTrade (or None when trade is rejected)
"""

from __future__ import annotations

import logging
from typing import Optional

from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.mev_gas_oracle import GasOracle, PFillEstimator, TipOptimizer
from apex_omega_core.core.types import (
    ExecutableTrade,
    ExecutionStats,
    MempoolState,
    OpportunityInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard filter constants
# ---------------------------------------------------------------------------

#: Minimum net profit (in USD) before the EV filter is applied.
MIN_PROFIT_USD: float = 1.0

#: Minimum execution probability required to trigger execution.
MIN_CONFIDENCE: float = 0.35

#: EV must be strictly greater than this threshold.
EV_THRESHOLD: float = 0.0

#: Assumed gas units for a Polygon flash-loan arbitrage transaction.
DEFAULT_GAS_UNITS: int = 350_000

#: USD cost charged when a transaction fails (gas burned without profit).
#: Approximated as the full gas cost, since failed txs still consume gas.
FAILURE_COST_MULTIPLIER: float = 1.0

# ---------------------------------------------------------------------------
# Risk buffer weights
# ---------------------------------------------------------------------------

#: Fraction of expected output reserved as base slippage buffer.
SLIPPAGE_BASE: float = 0.005          # 0.5 %

#: Scalar applied to tip_drift_gwei / 100 to produce mempool drift component.
MEMPOOL_DRIFT_SCALE: float = 0.002

#: Scalar applied to congestion_level to produce competition pressure component.
COMPETITION_PRESSURE_SCALE: float = 0.003

# ---------------------------------------------------------------------------
# P(exec) model weights  — must sum to 1.0
# ---------------------------------------------------------------------------

W_GAS_RANK: float = 0.30
W_ROUTE_COMPLEXITY: float = 0.25
W_MEMPOOL_DENSITY: float = 0.25
W_HISTORICAL_SUCCESS: float = 0.20


class EVEngine:
    """
    Merged C1 + C2 EV engine.

    Usage::

        engine = EVEngine()
        trade = engine.evaluate(opportunity_input)
        if trade is not None:
            # trade is EV-positive; pass to RouteEnvelopeBuilder
            ...
    """

    def __init__(
        self,
        min_profit_usd: float = MIN_PROFIT_USD,
        min_confidence: float = MIN_CONFIDENCE,
        ev_threshold: float = EV_THRESHOLD,
        gas_oracle: Optional[GasOracle] = None,
    ) -> None:
        self.sentinel = SlippageSentinel()
        self.gas_oracle = gas_oracle or GasOracle()
        self.min_profit_usd = min_profit_usd
        self.min_confidence = min_confidence
        self.ev_threshold = ev_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, inp: OpportunityInput) -> Optional[ExecutableTrade]:
        """Run the full 8-step EV pipeline.

        Returns an :class:`~apex_omega_core.core.types.ExecutableTrade` when the
        opportunity clears all hard filters, or ``None`` when it is rejected.
        """
        # ---- STEP 1: Simulate raw AMM output at candidate sizes --------
        optimized = self.sentinel.optimize(
            route=inp.route,
            min_input=inp.min_input,
            max_input=inp.max_input,
            steps=inp.optimize_steps,
            raw_spread=inp.raw_spread,
        )

        if optimized['profit'] == float('-inf'):
            logger.debug("EVEngine: route pruned by liquidity/health gates")
            return None

        amount_in: float = optimized['optimal_input']
        raw_out: float = optimized['final_output']

        # ---- STEP 2: Apply reality buffer ------------------------------
        risk_buffer = self._compute_risk_buffer(inp.mempool_state)
        safe_out = raw_out * (1.0 - risk_buffer)

        # ---- STEP 3: Optimal size already found by optimizer (Step 1) --
        # The grid-search in optimize() maximises net_profit_usd, which
        # implicitly solves for the optimal trade size X*.

        # ---- STEP 4: Gas-integrated net profit -------------------------
        # Use USD-denominated optimizer profit as gross profit so that
        # multi-token routes with different token prices are handled correctly.
        gross_profit_usd: float = max(0.0, float(optimized.get('net_profit_usd', 0.0)))
        gas_cost_usd = self._gas_cost_usd(inp.gas_estimate, inp.gas_price_gwei)
        net_profit = gross_profit_usd - gas_cost_usd

        # ---- STEP 5: Execution probability model -----------------------
        p_exec = self._estimate_p_exec(inp.gas_price_gwei, inp.route, inp.mempool_state, inp.historical_stats)

        # ---- STEP 6: Expected value calculation ------------------------
        failure_cost = gas_cost_usd * FAILURE_COST_MULTIPLIER
        ev = net_profit * p_exec - (1.0 - p_exec) * failure_cost

        # ---- STEP 7: Hard filters (non-negotiable) ---------------------
        if ev <= self.ev_threshold:
            logger.debug("EVEngine: rejected — EV=%.6f <= threshold %.6f", ev, self.ev_threshold)
            return None
        if net_profit <= self.min_profit_usd:
            logger.debug("EVEngine: rejected — net_profit=%.4f <= MIN_PROFIT_USD=%.4f", net_profit, self.min_profit_usd)
            return None
        if p_exec < self.min_confidence:
            logger.debug("EVEngine: rejected — p_exec=%.4f < MIN_CONFIDENCE=%.4f", p_exec, self.min_confidence)
            return None

        # ---- STEP 8: Return clean signal -------------------------------
        # min_out is the worst-acceptable final output after applying the
        # reality buffer; the envelope builder will cascade this per step.
        return ExecutableTrade(
            amount_in=amount_in,
            min_out=safe_out,
            expected_profit=gross_profit_usd,
            ev=ev,
            p_exec=p_exec,
            net_profit=net_profit,
            route=inp.route,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_risk_buffer(self, mempool: MempoolState) -> float:
        """Aggregate risk buffer: slippage_base + mempool_drift + competition_pressure.

        Clamped to [0.0, 0.20] to prevent safe_out from going negative on
        highly congested routes.
        """
        mempool_drift = MEMPOOL_DRIFT_SCALE * (abs(mempool.tip_drift_gwei) / 100.0)
        competition_pressure = COMPETITION_PRESSURE_SCALE * max(0.0, min(1.0, mempool.congestion_level))
        raw = SLIPPAGE_BASE + mempool_drift + competition_pressure
        return max(0.0, min(0.20, raw))

    def _gas_cost_usd(self, gas_estimate: float, gas_price_gwei: float) -> float:
        """Convert gas units × price (Gwei) to USD using TipOptimizer ETH price."""
        gas_wei = gas_estimate * gas_price_gwei * 1e9
        eth_used = gas_wei / 1e18
        return eth_used * TipOptimizer.ETH_PRICE_USD

    def _estimate_p_exec(
        self,
        gas_price_gwei: float,
        route: list,
        mempool: MempoolState,
        stats: ExecutionStats,
    ) -> float:
        """Weighted composite P(exec) from four independent signals.

        Signals
        -------
        gas_rank_score         – logistic P(fill) derived from the live fee-history model
        route_complexity_score – penalty for longer routes (more hops → lower P)
        mempool_density_score  – penalty for congested mempools
        historical_success_rate – calibrated from past execution outcomes
        """
        # 1. Gas rank score via PFillEstimator
        try:
            snapshot = self.gas_oracle.get_snapshot()
            estimator = PFillEstimator(snapshot)
            gas_rank_score = estimator.estimate(gas_price_gwei)
        except Exception:
            gas_rank_score = 0.5

        # 2. Route complexity penalty (linear; 1 hop → 1.0, 4 hops → 0.4)
        hop_count = max(1, len(route))
        route_complexity_score = max(0.0, 1.0 - (hop_count - 1) * 0.2)

        # 3. Mempool density penalty (linear from congestion_level)
        mempool_density_score = max(0.0, 1.0 - mempool.congestion_level)

        # 4. Historical success rate (clamped to valid probability range)
        historical_score = max(0.0, min(1.0, stats.historical_success_rate))

        p_exec = (
            W_GAS_RANK * gas_rank_score
            + W_ROUTE_COMPLEXITY * route_complexity_score
            + W_MEMPOOL_DENSITY * mempool_density_score
            + W_HISTORICAL_SUCCESS * historical_score
        )
        return max(0.0, min(1.0, p_exec))
