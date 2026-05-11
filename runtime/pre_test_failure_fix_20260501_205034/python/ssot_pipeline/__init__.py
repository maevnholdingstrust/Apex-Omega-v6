"""Dual Punch SSOT pipeline — Phase 1.

Standalone package implementing the locked 2-swap constant-product arbitrage
pipeline: math, audit, degradation simulation, batch simulation, and the
top-level finalizer.

Public API
----------
Types:
    RouteAuditResult, ExecutionRunResult, BatchSummary, PipelineFinalResult

Math:
    amm_swap, two_leg_arb_profit

Audit:
    audit_two_leg_route_envelope

Simulation:
    ExecutionDegradationSimulator, BatchSimulator

Pipeline:
    SSOTPipelineFinalizer
"""
from .audit import audit_two_leg_route_envelope
from .batch import BatchSimulator
from .degradation import ExecutionDegradationSimulator
from .finalizer import SSOTPipelineFinalizer
from .math_core import amm_swap, two_leg_arb_profit
from .types import (
    BatchSummary,
    ExecutionRunResult,
    PipelineFinalResult,
    RouteAuditResult,
)

__all__ = [
    "amm_swap",
    "two_leg_arb_profit",
    "audit_two_leg_route_envelope",
    "ExecutionDegradationSimulator",
    "BatchSimulator",
    "SSOTPipelineFinalizer",
    "RouteAuditResult",
    "ExecutionRunResult",
    "BatchSummary",
    "PipelineFinalResult",
]
