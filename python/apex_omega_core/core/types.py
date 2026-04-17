from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class Spread:
    symbol: str
    bid: float
    ask: float
    timestamp: float

@dataclass
class Slippage:
    expected_price: float
    actual_price: float
    difference: float

@dataclass
class Feature:
    name: str
    value: float

@dataclass
class InferenceResult:
    net_edge: float
    features: List[Feature]

@dataclass
class ExecutionResult:
    success: bool
    slippage: Optional[Slippage] = None
    tx_hash: Optional[str] = None

@dataclass
class Pool:
    address: str
    dex: str
    token0: str
    token1: str
    tvl_usd: float
    fee: float

@dataclass
class ArbitrageOpportunity:
    token: str
    buy_pool: Pool
    sell_pool: Pool
    buy_price: float
    sell_price: float
    spread_bps: float
    estimated_profit_usd: float
    flash_loan_amount: float
    flash_loan_token: str
    path: List[str]  # Up to 4 hops
    gas_estimate: float

@dataclass
class FlashLoanConfig:
    min_amount_usd: float = 5000.0
    max_pool_tvl_percent: float = 0.1  # 10%
    supported_providers: List[str] = None

    def __post_init__(self):
        if self.supported_providers is None:
            self.supported_providers = ['aave', 'balancer', 'uniswap']


@dataclass
class MempoolState:
    """Snapshot of mempool conditions at the time of opportunity evaluation."""
    pending_tx_count: int = 0
    competing_arb_count: int = 0
    # Fractional congestion level in [0.0, 1.0]; derived from gas_used_ratio_avg.
    congestion_level: float = 0.0
    # Average observed tip drift vs the p50 baseline (Gwei).
    tip_drift_gwei: float = 0.0


@dataclass
class ExecutionStats:
    """Historical execution statistics used for P(fill) weighting."""
    historical_success_rate: float = 0.8
    # Fraction of recent transactions that were included in the next block.
    next_block_inclusion_rate: float = 0.6
    # Average latency from broadcast to inclusion (ms).
    avg_latency_ms: float = 200.0
    # Total executions observed (used to weight success rate reliability).
    sample_count: int = 0


@dataclass
class OpportunityInput:
    """Unified input struct consumed by the EV Engine."""
    route: List[Dict[str, Any]]
    gas_price_gwei: float
    gas_estimate: float
    mempool_state: MempoolState
    historical_stats: ExecutionStats
    # Search range passed to the optimizer.
    min_input: float = 1_000.0
    max_input: float = 1_000_000.0
    raw_spread: float = 0.0
    optimize_steps: int = 100


@dataclass
class ExecutableTrade:
    """Output of the EV Engine — only produced for EV-positive opportunities."""
    amount_in: float
    min_out: float
    expected_profit: float
    ev: float
    p_exec: float
    net_profit: float
    route: List[Dict[str, Any]]