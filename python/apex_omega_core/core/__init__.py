# Core modules package

from .execution_compiler import CompiledExecution, EnvelopeCompiler, ExecutionCompiler, FlashloanPayloadBuilder
from .inference import profitability_gate, derive_net_edge
from .mev_gas_oracle import FeeHistory, GasOracle, GasPriceSnapshot, PFillEstimator, TipOptimizer
from .mev_bundle import BundleBuilder, BundleSimulator, BundleSubmitter, BundleTransaction, MEVBundle
from .mev_mempool_watcher import MempoolWatcher, MempoolStateSnapshot, PendingTx
from .execution_stats_accumulator import ExecutionStatsAccumulator, ExecutionOutcome
from .deterministic_slippage import calculate_deterministic_slippage_bps
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
from . import rpc_tester

__all__ = [
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
	# Deterministic CPMM slippage calculator
	"calculate_deterministic_slippage_bps",
	# SSOT full-stack pipeline
	"RouteAuditResult",
	"ExecutionRunResult",
	"BatchSummary",
	"PipelineFinalResult",
	"audit_two_leg_route_envelope",
	"ExecutionDegradationSimulator",
	"BatchSimulator",
	"SSOTPipelineFinalizer",
	# Live RPC endpoint helpers
	"rpc_tester",
]