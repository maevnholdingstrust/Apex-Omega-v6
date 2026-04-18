from dataclasses import dataclass, field
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


# ── Intake layer data model ───────────────────────────────────────────────────
# Python-side mirrors of the canonical Rust intake schema defined in
# src/intake.rs.  Use the Rust types (exposed via the apex_omega_core_rust
# extension) when performance matters; these dataclasses are provided for
# testing, serialisation, and pure-Python fallback paths.

# Feed A — Static metadata

@dataclass
class TokenMeta:
    """Slow-changing token metadata (Feed A)."""
    address: str
    decimals: int
    symbol: str
    chain_id: int


@dataclass
class RouterMeta:
    """Slow-changing router metadata (Feed A)."""
    address: str
    router_type: str          # "v2", "v3", "balancer", …
    dex_family: str
    selector_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class PoolMeta:
    """Slow-changing pool metadata (Feed A)."""
    address: str
    pool_type: str            # "v2" or "v3"
    dex_family: str
    fee_tier: float           # decimal, e.g. 0.003
    token0: str
    token1: str
    router_address: str


# Feed B — Live pool state

@dataclass
class PoolState:
    """Per-block pool state snapshot (Feed B).

    V2 pools populate ``reserve0`` / ``reserve1``.
    V3 pools populate ``sqrt_price_x96``, ``tick``, and ``liquidity``.
    Unused fields for the other pool type are left at ``0.0`` / ``0``.
    """
    pool_address: str
    block_number: int
    snapshot_timestamp_ms: int
    pool_type: str            # "v2" or "v3"
    reserve0: float = 0.0
    reserve1: float = 0.0
    sqrt_price_x96: float = 0.0
    tick: int = 0
    liquidity: float = 0.0
    is_callable: bool = True

    def has_v2_reserves(self) -> bool:
        return self.pool_type == "v2" and self.reserve0 > 0.0 and self.reserve1 > 0.0

    def has_v3_state(self) -> bool:
        return self.pool_type == "v3" and self.sqrt_price_x96 > 0.0 and self.liquidity > 0.0


# Feed C — Gas state

@dataclass
class GasState:
    """Per-block gas snapshot (Feed C)."""
    block_number: int
    snapshot_timestamp_ms: int
    base_fee_gwei: float
    priority_fee_p25_gwei: float
    priority_fee_p50_gwei: float
    priority_fee_p75_gwei: float
    gas_estimate_by_archetype: Dict[str, int] = field(default_factory=dict)

    def p_fill_sigma(self) -> float:
        """Derived sigma for the P(fill) logistic model: (p75 − p25) / 4."""
        return max((self.priority_fee_p75_gwei - self.priority_fee_p25_gwei) / 4.0, 1e-9)


# Feed D — Mempool / drift state

@dataclass
class MempoolState:
    """Sub-block mempool and reserve-drift snapshot (Feed D)."""
    snapshot_timestamp_ms: int
    pending_swap_count: int
    reserve_delta_forecast: Dict[str, float] = field(default_factory=dict)
    competing_bot_density: float = 0.0
    freshness_age_ms: int = 0

    def pool_delta(self, pool_address: str) -> float:
        """Forecast reserve delta for ``pool_address``, defaulting to 0.0."""
        return self.reserve_delta_forecast.get(pool_address, 0.0)


# Feed E — Historical execution stats

@dataclass
class ExecutionStats:
    """Rolling-window execution statistics (Feed E)."""
    window_size: int
    route_hit_rate: float
    revert_rate: float
    inclusion_rate: float
    realized_slippage_error_bps: float
    expected_vs_actual_pnl_error_bps: float
    per_router_failure_rates: Dict[str, float] = field(default_factory=dict)

    def p_exec_estimate(self) -> float:
        """Calibrated p_exec: inclusion_rate × (1 − revert_rate)."""
        return max(0.0, min(1.0, self.inclusion_rate * (1.0 - self.revert_rate)))


# Feed F — Route snapshot

@dataclass
class RouteHop:
    """A single hop in a multi-hop route (part of Feed F)."""
    pool_address: str
    token_in: str
    token_out: str
    fee_tier: float           # decimal, e.g. 0.003
    pool_type: str            # "v2" or "v3"


@dataclass
class RouteSnapshot:
    """Normalized route object — the canonical C1/C2 input (Feed F).

    ``is_valid`` must be checked before any arithmetic is performed.
    After Punch 1, call ``invalidate_post_punch()`` to prevent reuse.
    """
    route_id: str
    hops: List[RouteHop]
    input_token: str
    output_token: str
    min_input: float
    max_input: float
    evaluation_block_number: int
    evaluation_timestamp_ms: int
    is_valid: bool = True
    validity_flags: List[str] = field(default_factory=list)

    def hop_count(self) -> int:
        return len(self.hops)

    def fee_tiers(self) -> List[float]:
        return [h.fee_tier for h in self.hops]

    def invalidate_post_punch(self) -> None:
        """Mark this snapshot invalid after Punch 1; a full reload is required."""
        self.is_valid = False
        self.validity_flags.append(
            "invalidated_post_punch1: full state reload required before new cycle"
        )


# Aggregated hot-cache

@dataclass
class MarketState:
    """Aggregated hot-cache: pool states + gas state + static metadata.

    Feeds A, B, and C combined.  Keyed by ``block_number`` for staleness
    checks.
    """
    block_number: int
    snapshot_timestamp_ms: int
    pool_states: List[PoolState]
    gas_state: GasState
    token_metas: List[TokenMeta] = field(default_factory=list)
    pool_metas: List[PoolMeta] = field(default_factory=list)
    router_metas: List[RouterMeta] = field(default_factory=list)

    def get_pool_state(self, pool_address: str) -> Optional[PoolState]:
        return next((ps for ps in self.pool_states if ps.pool_address == pool_address), None)

    def get_token_meta(self, token_address: str) -> Optional[TokenMeta]:
        return next((tm for tm in self.token_metas if tm.address == token_address), None)

    def get_pool_meta(self, pool_address: str) -> Optional[PoolMeta]:
        return next((pm for pm in self.pool_metas if pm.address == pool_address), None)

    def get_router_meta(self, router_address: str) -> Optional[RouterMeta]:
        return next((rm for rm in self.router_metas if rm.address == router_address), None)


# Intake validation result

@dataclass
class IntakeAuditResult:
    """Result of a single intake audit pass."""
    audit_name: str
    passed: bool
    failures: List[str] = field(default_factory=list)