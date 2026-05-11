# Core modules package

from .deterministic_slippage import (
    calculate_deterministic_slippage_bps,
    calculate_cpmm_output_slippage_bps,
    calculate_real_profit,
    max_leg_slippage_bps,
)
from .execution_compiler import CompiledExecution, EnvelopeCompiler, ExecutionCompiler, FlashloanPayloadBuilder
from .protocol_swaps import (
    PROTOCOL_CUSTOM,
    PROTOCOL_UNISWAP_V2,
    PROTOCOL_UNISWAP_V3,
    PROTOCOL_ALGEBRA,
    PROTOCOL_CURVE,
    PROTOCOL_BALANCER,
    ProtocolSwapEncoder,
    min_amount_out_from_quote,
)
from .inference import profitability_gate, derive_net_edge
from .market_surface import (
    ExecutableMarketPoint,
    MarketDistanceOpportunity,
    SizeLadderPoint,
    build_market_distance_opportunity,
    build_size_ladder,
    market_opportunity_to_c1_packet,
    scan_market_surface,
)
from .market_surface_labels import classify_flash_ladder_zone, is_size_zone_allowed_for_c1
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
    "calculate_deterministic_slippage_bps",
    "calculate_cpmm_output_slippage_bps",
    "calculate_real_profit",
    "max_leg_slippage_bps",
    "CompiledExecution",
    "EnvelopeCompiler",
    "ExecutionCompiler",
    "FlashloanPayloadBuilder",
    "profitability_gate",
    "derive_net_edge",
    "ExecutableMarketPoint",
    "MarketDistanceOpportunity",
    "SizeLadderPoint",
    "build_market_distance_opportunity",
    "build_size_ladder",
    "market_opportunity_to_c1_packet",
    "scan_market_surface",
    "classify_flash_ladder_zone",
    "is_size_zone_allowed_for_c1",
    "FeeHistory",
    "GasOracle",
    "GasPriceSnapshot",
    "PFillEstimator",
    "TipOptimizer",
    "BundleBuilder",
    "BundleSimulator",
    "BundleSubmitter",
    "BundleTransaction",
    "MEVBundle",
    "MempoolWatcher",
    "MempoolStateSnapshot",
    "PendingTx",
    "ExecutionStatsAccumulator",
    "ExecutionOutcome",
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
