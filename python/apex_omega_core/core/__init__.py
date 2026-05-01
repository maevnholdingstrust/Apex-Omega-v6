# Core modules package

from .deterministic_slippage import (
    calculate_deterministic_slippage_bps,
    calculate_cpmm_output_slippage_bps,
    max_leg_slippage_bps,
)
from .execution_compiler import CompiledExecution, EnvelopeCompiler, ExecutionCompiler, FlashloanPayloadBuilder
from .inference import profitability_gate, derive_net_edge
from .mev_gas_oracle import FeeHistory, GasOracle, GasPriceSnapshot, PFillEstimator, TipOptimizer
from .mev_bundle import BundleBuilder, BundleSimulator, BundleSubmitter, BundleTransaction, MEVBundle
from .mev_mempool_watcher import MempoolWatcher, MempoolStateSnapshot, PendingTx
from .execution_stats_accumulator import ExecutionStatsAccumulator, ExecutionOutcome
from .ssot_pipeline import (
    RouteAuditResult,
    ExecutionRunResult,
    BatchSummary,
    PipelineFinalResult,
    audit_two_leg_route_envelope,
    ExecutionDegradationSimulator,
    BatchSimulator,
    SSOTPipelineFinalizer,
)
from .route_graph import CycleRecord, RouteGraph, scan_multi_hop_cycles, simulate_n_hop_cycle
from .expanded_graph_scan import (
    ScoredRoute,
    ScanCandidate,
    ExpandedGraphScanResult,
    expanded_graph_scan,
)
from .token_universe import TokenUniverse, SEED_TOKENS, get_seed_tokens, get_seed_pairs
from . import rpc_tester

__all__ = [
    # Deterministic CPMM slippage (SSOT — replaces heuristic predict_sigma)
    "calculate_deterministic_slippage_bps",
    "calculate_cpmm_output_slippage_bps",
    "max_leg_slippage_bps",
    "CompiledExecution",
    "EnvelopeCompiler",
    "ExecutionCompiler",
    "FlashloanPayloadBuilder",
    # Profitability gate (SSOT for P_net × P(fill) > 0)
    "profitability_gate",
    "derive_net_edge",
    # MEV gas oracle
    "FeeHistory",
    "GasOracle",
    "GasPriceSnapshot",
    "PFillEstimator",
    "TipOptimizer",
    # MEV bundle
    "BundleBuilder",
    "BundleSimulator",
    "BundleSubmitter",
    "BundleTransaction",
    "MEVBundle",
    # Live Feed D — mempool watcher
    "MempoolWatcher",
    "MempoolStateSnapshot",
    "PendingTx",
    # Live Feed E — execution stats accumulator
    "ExecutionStatsAccumulator",
    "ExecutionOutcome",
    # SSOT full-stack pipeline
    "RouteAuditResult",
    "ExecutionRunResult",
    "BatchSummary",
    "PipelineFinalResult",
    "audit_two_leg_route_envelope",
    "ExecutionDegradationSimulator",
    "BatchSimulator",
    "SSOTPipelineFinalizer",
    # N-hop route graph
    "CycleRecord",
    "RouteGraph",
    "scan_multi_hop_cycles",
    "simulate_n_hop_cycle",
    # Expanded graph scan
    "ScoredRoute",
    "ScanCandidate",
    "ExpandedGraphScanResult",
    "expanded_graph_scan",
    # Token universe
    "TokenUniverse",
    "SEED_TOKENS",
    "get_seed_tokens",
    "get_seed_pairs",
    # Live RPC endpoint helpers
    "rpc_tester",
]