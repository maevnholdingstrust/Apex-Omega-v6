# Core modules package

from .contract_invoker import (
    ContractInvoker,
    TokenUnitSpec,
    attach_flashloan_token_meta,
    resolve_min_final_output_units,
    resolve_optimal_input_units,
    usd_to_native_wei,
)
from .contract_targets import C1_TARGET, C2_TARGET
from .dashboard_coordinator import BroadcastFn, DashboardCoordinator
from .deterministic_slippage import (
    calculate_deterministic_slippage_bps,
    calculate_cpmm_output_slippage_bps,
    max_leg_slippage_bps,
)
from .execution_compiler import CompiledExecution, EnvelopeCompiler, ExecutionCompiler, FlashloanPayloadBuilder
from .execution_stats_accumulator import ExecutionStatsAccumulator, ExecutionOutcome
from .feature_factory import extract_features
from .inference import profitability_gate, derive_net_edge
from .mev_bundle import BundleBuilder, BundleSimulator, BundleSubmitter, BundleTransaction, MEVBundle
from .mev_gas_oracle import FeeHistory, GasOracle, GasPriceSnapshot, PFillEstimator, TipOptimizer
from .mev_mempool_watcher import MempoolWatcher, MempoolStateSnapshot, PendingTx
from .polygon_arbitrage import ArbitrageDetector, PolygonDEXMonitor
from .scanner_surface import (
    build_c1_intake,
    build_token_summary,
    compute_market_extrema,
    group_rows_by_token,
    pool_to_venue_row,
    should_recompute,
)
from .slippage_sentinel import SlippageSentinel
from .spread_alignment import (
    align_spread,
    bps_to_decimal,
    compute_raw_spread,
    compute_raw_spread_bps,
    decimal_to_bps,
)
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
from .types import (
    ArbitrageOpportunity,
    ExecutionResult,
    ExecutionStats,
    Feature,
    FlashLoanConfig,
    GasState,
    InferenceResult,
    IntakeAuditResult,
    MarketExtrema,
    MarketState,
    MempoolState,
    Pool,
    PoolMeta,
    PoolState,
    RouteHop,
    RouteSnapshot,
    RouterMeta,
    Slippage,
    Spread,
    TokenMarketSurface,
    TokenMeta,
    TokenSummaryRow,
    VenueQuoteRow,
)
from . import rpc_tester

__all__ = [
    # Contract targets
    "C1_TARGET",
    "C2_TARGET",
    # Contract invoker
    "ContractInvoker",
    "TokenUnitSpec",
    "attach_flashloan_token_meta",
    "resolve_min_final_output_units",
    "resolve_optimal_input_units",
    "usd_to_native_wei",
    # Dashboard coordinator
    "BroadcastFn",
    "DashboardCoordinator",
    # Deterministic CPMM slippage (SSOT — replaces heuristic predict_sigma)
    "calculate_deterministic_slippage_bps",
    "calculate_cpmm_output_slippage_bps",
    "max_leg_slippage_bps",
    # Execution compiler
    "CompiledExecution",
    "EnvelopeCompiler",
    "ExecutionCompiler",
    "FlashloanPayloadBuilder",
    # Live Feed E — execution stats accumulator
    "ExecutionStatsAccumulator",
    "ExecutionOutcome",
    # Feature factory
    "extract_features",
    # Profitability gate (SSOT for P_net × P(fill) > 0)
    "profitability_gate",
    "derive_net_edge",
    # MEV bundle
    "BundleBuilder",
    "BundleSimulator",
    "BundleSubmitter",
    "BundleTransaction",
    "MEVBundle",
    # MEV gas oracle
    "FeeHistory",
    "GasOracle",
    "GasPriceSnapshot",
    "PFillEstimator",
    "TipOptimizer",
    # Live Feed D — mempool watcher
    "MempoolWatcher",
    "MempoolStateSnapshot",
    "PendingTx",
    # Polygon DEX monitor + arbitrage detector
    "ArbitrageDetector",
    "PolygonDEXMonitor",
    # Scanner surface functions
    "build_c1_intake",
    "build_token_summary",
    "compute_market_extrema",
    "group_rows_by_token",
    "pool_to_venue_row",
    "should_recompute",
    # Slippage sentinel
    "SlippageSentinel",
    # Spread alignment helpers
    "align_spread",
    "bps_to_decimal",
    "compute_raw_spread",
    "compute_raw_spread_bps",
    "decimal_to_bps",
    # SSOT full-stack pipeline
    "RouteAuditResult",
    "ExecutionRunResult",
    "BatchSummary",
    "PipelineFinalResult",
    "audit_two_leg_route_envelope",
    "ExecutionDegradationSimulator",
    "BatchSimulator",
    "SSOTPipelineFinalizer",
    # Public data types (Feeds A–F + scanner surface)
    "ArbitrageOpportunity",
    "ExecutionResult",
    "ExecutionStats",
    "Feature",
    "FlashLoanConfig",
    "GasState",
    "InferenceResult",
    "IntakeAuditResult",
    "MarketExtrema",
    "MarketState",
    "MempoolState",
    "Pool",
    "PoolMeta",
    "PoolState",
    "RouteHop",
    "RouteSnapshot",
    "RouterMeta",
    "Slippage",
    "Spread",
    "TokenMarketSurface",
    "TokenMeta",
    "TokenSummaryRow",
    "VenueQuoteRow",
    # Live RPC endpoint helpers
    "rpc_tester",
]