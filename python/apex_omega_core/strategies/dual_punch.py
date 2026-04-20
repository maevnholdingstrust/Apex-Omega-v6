"""Dual Punch — official modular sequential two-stage arbitrage strategy.

Strategy summary
----------------
Punch 1 maximises executable EV on the live market state s0.
Punch 2 maximises executable EV on the state s1 produced by Punch 1, using the
best of same / reverse / alternate / no-trade route selection.

Canonical EV equations (spec-locked)
-------------------------------------
  F(x)     = x * flash_fee_rate
  O_safe   = route_output * (1 - beta)          # multiplicative safety buffer
  Π_net    = O_safe - x - F(x) - G              # net executable profit
  EV       = p * Π_net - (1 - p) * L

Strike rules
------------
  Execute iff  EV > 0  and  Π_net > MinProfit  and  p >= p_min

State mutation bridge (Module B)
---------------------------------
  s1 = T(s0, x1, r1): each hop's reserves are updated by the constant-product
  AMM formula after executing the optimal Punch-1 size x1 through route r1.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from apex_omega_core.core.slippage_sentinel import SlippageSentinel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter and result types
# ---------------------------------------------------------------------------

@dataclass
class DualPunchParams:
    """All tunable parameters for a single Dual Punch cycle.

    Parameters are independent for each punch so the engine can be calibrated
    per-punch without coupling the two stages.
    """
    # Punch 1
    p1_success: float = 0.95           # success probability
    failure_loss1: float = 0.0         # USD loss on failure
    gas_cost1: float = 0.0             # gas cost in USD
    flash_fee_rate1: float = 0.0009    # flash loan fee as decimal (9 bps default)
    safety_beta1: float = 0.005        # multiplicative safety buffer (0.5%)
    min_profit1: float = 0.0           # minimum net profit threshold in USD
    p1_min: float = 0.0                # minimum allowed success probability

    # Punch 2
    p2_success: float = 0.95
    failure_loss2: float = 0.0
    gas_cost2: float = 0.0
    flash_fee_rate2: float = 0.0009
    safety_beta2: float = 0.005
    min_profit2: float = 0.0
    p2_min: float = 0.0


@dataclass
class PunchResult:
    """Evaluation result for a single punch."""
    ev: float
    gross_profit: float
    net_profit: float
    safe_output: float
    optimal_input: float
    flash_fee: float
    gas_cost: float
    route_type: str                       # 'same' | 'reverse' | 'alternate' | 'none'
    route: List[Dict[str, Any]]
    sentinel_output: Dict[str, Any]
    should_strike: bool
    reason: str = ""


@dataclass
class DualPunchCycleResult:
    """Full result for one Dual Punch cycle."""
    punch1: PunchResult
    punch2: Optional[PunchResult]         # None when Punch 1 rejected cycle
    s1_route: List[Dict[str, Any]]        # post-Punch-1 route (state mutation output)
    ev_cycle: float                       # EV1 + I2 * EV2
    pnl_cycle: float                     # realised PnL (0.0 until execution confirmed)
    cycle_log: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dual Punch Engine
# ---------------------------------------------------------------------------

class DualPunchEngine:
    """Implements the three-module Dual Punch strategy.

    Module A — Punch 1 evaluator
        Input : discovery route + state s0
        Output: best (r1, x1*, EV1); executes iff EV1 > 0

    Module B — State mutation checkpoint
        Input : Punch-1 execution (x1, r1, s0)
        Output: new state s1 = T(s0, x1, r1)

    Module C — Punch 2 evaluator
        Input : state s1
        Output: best (r2, x2*, EV2) over {same, reverse, alternate, none};
                executes iff EV2 > 0, otherwise NO-OP + LOG
    """

    def __init__(self) -> None:
        self.sentinel = SlippageSentinel()

    # ------------------------------------------------------------------
    # Flash fee helper
    # ------------------------------------------------------------------

    def _flash_fee(self, x: float, flash_fee_rate: float) -> float:
        """F(x) = x * flash_fee_rate."""
        return max(0.0, float(x) * float(flash_fee_rate))

    # ------------------------------------------------------------------
    # Safety-adjusted output helper
    # ------------------------------------------------------------------

    def _safe_output(self, raw_output: float, beta: float) -> float:
        """O_safe = O * (1 - beta).  Multiplicative safety buffer."""
        return float(raw_output) * max(0.0, 1.0 - float(beta))

    # ------------------------------------------------------------------
    # EV and net profit helpers
    # ------------------------------------------------------------------

    def _compute_net_profit(
        self,
        safe_output: float,
        x: float,
        flash_fee: float,
        gas_cost: float,
    ) -> float:
        """Π_net = O_safe - x - F(x) - G."""
        return safe_output - float(x) - float(flash_fee) - float(gas_cost)

    def _compute_ev(
        self,
        net_profit: float,
        p_success: float,
        failure_loss: float,
    ) -> float:
        """EV = p * Π_net - (1 - p) * L."""
        p = max(0.0, min(1.0, float(p_success)))
        return p * float(net_profit) - (1.0 - p) * max(0.0, float(failure_loss))

    # ------------------------------------------------------------------
    # Strike rule
    # ------------------------------------------------------------------

    def _strike_decision(
        self,
        ev: float,
        net_profit: float,
        p_success: float,
        min_profit: float,
        p_min: float,
    ) -> tuple[bool, str]:
        """Return (should_strike, reason) per the canonical strike rules."""
        if p_success < p_min:
            return False, f"p_success {p_success:.4f} < p_min {p_min:.4f}"
        if net_profit <= min_profit:
            return False, f"net_profit {net_profit:.6f} <= min_profit {min_profit:.6f}"
        if ev <= 0.0:
            return False, f"EV {ev:.6f} <= 0"
        return True, "EV > 0, Π_net > MinProfit, p >= p_min"

    # ------------------------------------------------------------------
    # Module B — state mutation T(s0, x1, r1) → s1
    # ------------------------------------------------------------------

    def mutate_state(
        self,
        route: List[Dict[str, Any]],
        x1: float,
    ) -> List[Dict[str, Any]]:
        """Apply Punch-1 execution to produce the post-impact route state s1.

        For each hop the constant-product AMM formula updates reserves:
          amount_in_with_fee = amount * (1 - fee)
          amount_out         = (amount_in_with_fee * R_out) / (R_in + amount_in_with_fee)
          new R_in           = R_in + amount_in_with_fee
          new R_out          = R_out - amount_out

        The resulting route represents s1 = T(s0, x1, r1) and is the state
        Module C must use for Punch-2 evaluation.
        """
        s1_route = copy.deepcopy(route)
        amount = float(x1)

        for leg in s1_route:
            reserve_in = float(leg.get('reserve_in', 0.0))
            reserve_out = float(leg.get('reserve_out', 0.0))
            fee = float(leg.get('fee', 0.003))

            if reserve_in <= 0.0 or reserve_out <= 0.0 or amount <= 0.0:
                break

            amount_with_fee = amount * (1.0 - fee)
            amount_out = (amount_with_fee * reserve_out) / (reserve_in + amount_with_fee)

            leg['reserve_in'] = reserve_in + amount_with_fee
            leg['reserve_out'] = max(0.0, reserve_out - amount_out)

            amount = amount_out

        return s1_route

    # ------------------------------------------------------------------
    # Single punch EV evaluator (shared by both punches)
    # ------------------------------------------------------------------

    def _evaluate_punch(
        self,
        route: List[Dict[str, Any]],
        route_type: str,
        p_success: float,
        failure_loss: float,
        gas_cost: float,
        flash_fee_rate: float,
        safety_beta: float,
        min_profit: float,
        p_min: float,
        min_input: float,
        max_input: float,
        steps: int,
        raw_spread: float,
    ) -> PunchResult:
        """Compute EV for one punch on the given route and state."""
        sentinel_output = self.sentinel.optimize(
            route,
            min_input=min_input,
            max_input=max_input,
            steps=steps,
            raw_spread=raw_spread,
        )

        x = float(sentinel_output['optimal_input'])
        raw_output = float(sentinel_output['final_output'])

        flash_fee = self._flash_fee(x, flash_fee_rate)
        safe_output = self._safe_output(raw_output, safety_beta)
        gross_profit = raw_output - x - flash_fee
        net_profit = self._compute_net_profit(safe_output, x, flash_fee, gas_cost)
        ev = self._compute_ev(net_profit, p_success, failure_loss)
        should_strike, reason = self._strike_decision(ev, net_profit, p_success, min_profit, p_min)

        return PunchResult(
            ev=ev,
            gross_profit=gross_profit,
            net_profit=net_profit,
            safe_output=safe_output,
            optimal_input=x,
            flash_fee=flash_fee,
            gas_cost=gas_cost,
            route_type=route_type,
            route=route,
            sentinel_output=sentinel_output,
            should_strike=should_strike,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Module A — Punch 1 evaluator
    # ------------------------------------------------------------------

    def evaluate_punch1(
        self,
        route: List[Dict[str, Any]],
        params: DualPunchParams,
        min_input: float = 1_000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> PunchResult:
        """Module A: compute EV1 on state s0.

        Returns the best (r1, x1*, EV1) result with should_strike indicating
        whether to execute Punch 1.
        """
        return self._evaluate_punch(
            route=route,
            route_type='same',
            p_success=params.p1_success,
            failure_loss=params.failure_loss1,
            gas_cost=params.gas_cost1,
            flash_fee_rate=params.flash_fee_rate1,
            safety_beta=params.safety_beta1,
            min_profit=params.min_profit1,
            p_min=params.p1_min,
            min_input=min_input,
            max_input=max_input,
            steps=steps,
            raw_spread=raw_spread,
        )

    # ------------------------------------------------------------------
    # Module C — Punch 2 evaluator
    # ------------------------------------------------------------------

    def evaluate_punch2(
        self,
        s1_route: List[Dict[str, Any]],
        params: DualPunchParams,
        alternate_routes: Optional[List[List[Dict[str, Any]]]] = None,
        min_input: float = 1_000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> PunchResult:
        """Module C: compute EV2 on state s1 across route variants.

        Tries same / reverse / alternate (if provided) and returns the variant
        with the highest EV.  Returns a no-op result when no variant passes the
        strike rules.

        r2 ∈ {same, reverse, alternate, none} is the route selection outcome.
        """
        reverse_route = self.sentinel.reverse_route(s1_route)

        candidates: List[tuple[str, List[Dict[str, Any]]]] = [
            ('same', s1_route),
            ('reverse', reverse_route),
        ]
        for i, alt in enumerate(alternate_routes or []):
            candidates.append((f'alternate_{i}', alt))

        best: Optional[PunchResult] = None

        for route_type, route in candidates:
            result = self._evaluate_punch(
                route=route,
                route_type=route_type,
                p_success=params.p2_success,
                failure_loss=params.failure_loss2,
                gas_cost=params.gas_cost2,
                flash_fee_rate=params.flash_fee_rate2,
                safety_beta=params.safety_beta2,
                min_profit=params.min_profit2,
                p_min=params.p2_min,
                min_input=min_input,
                max_input=max_input,
                steps=steps,
                raw_spread=raw_spread,
            )
            if best is None or result.ev > best.ev:
                best = result

        # If no candidate has positive EV, return a no-op result.
        if best is None or not best.should_strike:
            no_op = PunchResult(
                ev=best.ev if best else 0.0,
                gross_profit=best.gross_profit if best else 0.0,
                net_profit=best.net_profit if best else 0.0,
                safe_output=best.safe_output if best else 0.0,
                optimal_input=best.optimal_input if best else 0.0,
                flash_fee=best.flash_fee if best else 0.0,
                gas_cost=params.gas_cost2,
                route_type='none',
                route=s1_route,
                sentinel_output=best.sentinel_output if best else {},
                should_strike=False,
                reason=best.reason if best else 'no candidates evaluated',
            )
            return no_op

        return best

    # ------------------------------------------------------------------
    # Full cycle orchestration
    # ------------------------------------------------------------------

    def run_dual_punch_cycle(
        self,
        route: List[Dict[str, Any]],
        params: DualPunchParams,
        alternate_routes: Optional[List[List[Dict[str, Any]]]] = None,
        min_input: float = 1_000.0,
        max_input: float = 1_000_000.0,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> DualPunchCycleResult:
        """Run a full Dual Punch cycle.

        Flow:
          1. Discovery → Punch 1 EV computation on s0 (Module A)
          2. Execute Punch 1 if approved
          3. State mutation: s1 = T(s0, x1, r1)  (Module B)
          4. Punch 2 EV computation on s1 with route selection (Module C)
          5. Execute Punch 2 if approved, otherwise NO-OP + LOG
          6. Return cycle result
        """
        cycle_log: List[str] = []

        # Module A — Punch 1
        punch1 = self.evaluate_punch1(
            route=route,
            params=params,
            min_input=min_input,
            max_input=max_input,
            steps=steps,
            raw_spread=raw_spread,
        )
        cycle_log.append(
            f"Punch1 EV={punch1.ev:.6f} Π_net={punch1.net_profit:.6f} "
            f"x1*={punch1.optimal_input:.2f} strike={punch1.should_strike} | {punch1.reason}"
        )

        if not punch1.should_strike:
            # Cycle rejected — Punch 2 does not run
            cycle_log.append("Cycle rejected: Punch 1 EV <= 0. No Punch 2.")
            return DualPunchCycleResult(
                punch1=punch1,
                punch2=None,
                s1_route=route,
                ev_cycle=punch1.ev,
                pnl_cycle=0.0,
                cycle_log=cycle_log,
            )

        # Module B — State mutation: s1 = T(s0, x1*, r1)
        s1_route = self.mutate_state(route, punch1.optimal_input)
        cycle_log.append(
            f"State mutation complete: s1 produced from x1*={punch1.optimal_input:.2f} "
            f"on {len(s1_route)}-hop route."
        )

        # Module C — Punch 2 (runs on s1, not s0)
        punch2 = self.evaluate_punch2(
            s1_route=s1_route,
            params=params,
            alternate_routes=alternate_routes,
            min_input=min_input,
            max_input=max_input,
            steps=steps,
            raw_spread=raw_spread,
        )
        cycle_log.append(
            f"Punch2 EV={punch2.ev:.6f} Π_net={punch2.net_profit:.6f} "
            f"x2*={punch2.optimal_input:.2f} route_type={punch2.route_type} "
            f"strike={punch2.should_strike} | {punch2.reason}"
        )

        if not punch2.should_strike:
            cycle_log.append("Punch 2: NO-OP. Post-impact recomputation found no positive EV.")

        # Decision-time cycle EV: EV_cycle = EV1 + I2 * EV2
        i2 = 1 if punch2.should_strike else 0
        ev_cycle = punch1.ev + i2 * punch2.ev
        cycle_log.append(f"Cycle EV = {ev_cycle:.6f} (I2={i2})")

        return DualPunchCycleResult(
            punch1=punch1,
            punch2=punch2,
            s1_route=s1_route,
            ev_cycle=ev_cycle,
            pnl_cycle=0.0,   # populated by execution layer after on-chain confirmation
            cycle_log=cycle_log,
        )
