"""Full-stack SSOT pipeline for the 2-leg constant-product arbitrage reference model.

This module ties together the canonical math (SlippageSentinel), execution
decision gate (profitability_gate), payload audit, and probabilistic simulation
into a single non-drifting reference implementation for the locked 2-swap
constant-product A→B→A cycle.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

from .inference import profitability_gate
from .slippage_sentinel import SlippageSentinel

@dataclass
class RouteAuditResult:
    passed: bool
    violations: List[str] = field(default_factory=list)

@dataclass
class ExecutionRunResult:
    a_in: float
    b_out_1: float
    a_out_2: float
    p_gross_deterministic: float
    p_net_deterministic: float
    p_net_actual: float
    c2_decision: str
    audit: RouteAuditResult

@dataclass
class BatchSummary:
    n_runs: int
    n_strikes: int
    n_profitable_strikes: int
    total_actual_profit: float
    mean_actual_profit_per_run: float
    hit_rate: float
    ev: float

@dataclass
class PipelineFinalResult:
    best_size: float
    p_net_deterministic: float
    ev: float
    c2_decision: str
    audit: RouteAuditResult
    batch_summary: BatchSummary

def audit_two_leg_route_envelope(a_in: float, fee1: float, b_out_1: float, b_in_2: float, fee2: float, a_out_2: float, p_gross: float, p_net: float, c_total_exec: float, tolerance: float = 1e-9) -> RouteAuditResult:
    violations: List[str] = []
    if abs(b_in_2 - b_out_1) > tolerance:
        violations.append(f"inventory_drift: b_in_2={b_in_2:.10f} != b_out_1={b_out_1:.10f} (delta={b_in_2 - b_out_1:.2e})")
    expected_p_gross = a_out_2 - a_in
    if abs(p_gross - expected_p_gross) > tolerance:
        violations.append(f"p_gross_mismatch: declared={p_gross:.10f}, expected A_out_2 - A_in={expected_p_gross:.10f} (delta={p_gross - expected_p_gross:.2e})")
    expected_p_net = p_gross
    if abs(p_net - expected_p_net) > tolerance:
        violations.append(f"p_net_mismatch: declared={p_net:.10f}, expected P_gross_exec - C_total_exec={expected_p_net:.10f} (delta={p_net - expected_p_net:.2e})")
    if fee1 < 0.0 or fee1 >= 1.0:
        violations.append(f"fee1_range: fee1={fee1} is outside [0, 1)")
    if fee2 < 0.0 or fee2 >= 1.0:
        violations.append(f"fee2_range: fee2={fee2} is outside [0, 1)")
    return RouteAuditResult(passed=len(violations) == 0, violations=violations)

class ExecutionDegradationSimulator:
    def __init__(self, degradation_mean: float = 0.65, degradation_std: float = 0.35, rng: Optional[random.Random] = None) -> None:
        self.degradation_mean = float(degradation_mean)
        self.degradation_std = float(degradation_std)
        self._rng = rng if rng is not None else random.Random()

    def _sample_degradation_factor(self) -> float:
        return max(0.0, self._rng.gauss(self.degradation_mean, self.degradation_std))

    def simulate_one_run(self, a_in: float, b_out_1: float, a_out_2: float, p_gross: float, p_net_deterministic: float, c_total_exec: float, p_fill: float, c2_decision: str, fee1: float = 0.0, fee2: float = 0.0) -> ExecutionRunResult:
        audit = audit_two_leg_route_envelope(a_in=a_in, fee1=fee1, b_out_1=b_out_1, b_in_2=b_out_1, fee2=fee2, a_out_2=a_out_2, p_gross=p_gross, p_net=p_net_deterministic, c_total_exec=c_total_exec)
        if c2_decision != "STRIKE":
            return ExecutionRunResult(a_in, b_out_1, a_out_2, p_gross, p_net_deterministic, 0.0, c2_decision, audit)
        return ExecutionRunResult(a_in, b_out_1, a_out_2, p_gross, p_net_deterministic, p_net_deterministic * self._sample_degradation_factor(), c2_decision, audit)

class BatchSimulator:
    def __init__(self, sentinel: SlippageSentinel, degradation_simulator: ExecutionDegradationSimulator) -> None:
        self._sentinel = sentinel
        self._deg_sim = degradation_simulator

    def run(self, a_in: float, fee1: float, r1_in: float, r1_out: float, fee2: float, r2_in: float, r2_out: float, c_total_exec: float, p_fill: float, n_runs: int) -> BatchSummary:
        math = self._sentinel.two_leg_arb_profit(a_in=a_in, fee1=fee1, r1_in=r1_in, r1_out=r1_out, fee2=fee2, r2_in=r2_in, r2_out=r2_out, c_gas=c_total_exec)
        p_net_det = math["p_net"]
        owner_submission_edge = math.get("owner_submission_edge", p_net_det - c_total_exec)
        ev = owner_submission_edge * p_fill
        c2_decision = "STRIKE" if profitability_gate(owner_submission_edge, p_fill) else "DO_NOTHING"
        total_actual_profit = 0.0
        n_strikes = 0
        n_profitable_strikes = 0
        for _ in range(n_runs):
            run_result = self._deg_sim.simulate_one_run(float(a_in), math["b_out_1"], math["a_out_2"], math["p_gross"], p_net_det, c_total_exec, p_fill, c2_decision, fee1, fee2)
            total_actual_profit += run_result.p_net_actual
            if c2_decision == "STRIKE":
                n_strikes += 1
                if run_result.p_net_actual > 0.0:
                    n_profitable_strikes += 1
        hit_rate = n_profitable_strikes / n_strikes if n_strikes > 0 else 0.0
        mean_actual_profit_per_run = total_actual_profit / n_runs if n_runs > 0 else 0.0
        return BatchSummary(n_runs, n_strikes, n_profitable_strikes, total_actual_profit, mean_actual_profit_per_run, hit_rate, ev)

class SSOTPipelineFinalizer:
    def __init__(self, sizes_to_test: List[float], n_batch_runs: int = 100, p_fill: float = 1.0, degradation_mean: float = 0.65, degradation_std: float = 0.35, rng_seed: Optional[int] = None) -> None:
        if not sizes_to_test:
            raise ValueError("sizes_to_test must contain at least one candidate size")
        self.sizes_to_test = list(sizes_to_test)
        self.n_batch_runs = int(n_batch_runs)
        self.p_fill = float(p_fill)
        rng = random.Random(rng_seed)
        self._sentinel = SlippageSentinel()
        self._deg_sim = ExecutionDegradationSimulator(degradation_mean, degradation_std, rng)
        self._batch_sim = BatchSimulator(self._sentinel, self._deg_sim)

    def run(self, fee1: float, r1_in: float, r1_out: float, fee2: float, r2_in: float, r2_out: float, c_total_exec: float = 0.0, **metadata) -> PipelineFinalResult:
        # ``metadata`` intentionally accepts live-state diagnostics such as rpc_url,
        # raw_spread_bps, implied prices, and pool labels exported by rpc_tester.
        # They are not part of the canonical 2-leg CPMM math input, so they are ignored here.
        best_size: Optional[float] = None
        best_p_net = float("-inf")
        best_math: Optional[dict] = None
        for size in self.sizes_to_test:
            math = self._sentinel.two_leg_arb_profit(a_in=size, fee1=fee1, r1_in=r1_in, r1_out=r1_out, fee2=fee2, r2_in=r2_in, r2_out=r2_out, c_gas=c_total_exec)
            ranking_edge = math.get("owner_submission_edge", math["p_net"] - c_total_exec)
            if ranking_edge > best_p_net:
                best_p_net = math["p_net"]
                best_size = size
                best_math = math
        if best_size is None or best_math is None:
            raise ValueError("No valid candidate size found in sizes_to_test")
        audit = audit_two_leg_route_envelope(best_size, fee1, best_math["b_out_1"], best_math["b_out_1"], fee2, best_math["a_out_2"], best_math["p_gross"], best_math["p_net"], c_total_exec)
        owner_submission_edge = best_math.get("owner_submission_edge", best_p_net - c_total_exec)
        ev = owner_submission_edge * self.p_fill
        c2_decision = "STRIKE" if profitability_gate(owner_submission_edge, self.p_fill) else "DO_NOTHING"
        batch_summary = self._batch_sim.run(best_size, fee1, r1_in, r1_out, fee2, r2_in, r2_out, c_total_exec, self.p_fill, self.n_batch_runs)
        return PipelineFinalResult(best_size, best_p_net, ev, c2_decision, audit, batch_summary)
