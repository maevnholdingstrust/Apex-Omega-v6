"""
DNA Card Data Schema

Pydantic models for all DNA card fields as specified in the dry-run dashboard patch.

Models:
    RouteProfile: Token route and DEX information
    ReservesState: Pool reserve data
    DiscoveryPricing: USD-normalized pricing
    CostStack: Execution cost breakdown
    RouteEnvelope: Full route payload details
    Decision: C1/C2 decision data
    Payload: Built payload information
    EVProbability: Probability and EV calculations
    Audit: Audit and validation results
    Dashboard: Dashboard display metadata
    Replay: Replay command information
    C1AggressorCard: Full C1 card model
    C2SurgeonCard: Full C2 card model
    CyclePair: Paired C1/C2 cycle
    BlockCycle: Block-level aggregate
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StrikeRole(str, Enum):
    C1 = "C1"
    C2 = "C2"


class StrikeName(str, Enum):
    AGGRESSOR = "Aggressor"
    SURGEON = "Surgeon"


class DecisionType(str, Enum):
    BUILD_PAYLOAD = "BUILD_PAYLOAD"
    EXECUTE = "EXECUTE"
    NO_OP = "NO_OP"


class RealizedStatus(str, Enum):
    DRY_RUN_NO_BROADCAST = "DRY_RUN_NO_BROADCAST"
    LIVE_REALIZED = "LIVE_REALIZED"
    LIVE_REVERTED = "LIVE_REVERTED"
    LIVE_DROPPED = "LIVE_DROPPED"
    LIVE_UNKNOWN = "LIVE_UNKNOWN"


class ShadowExecutionStatus(str, Enum):
    APPLIED_TO_SHADOW_STATE = "APPLIED_TO_SHADOW_STATE"
    NOT_APPLIED = "NOT_APPLIED"


class PoolType(str, Enum):
    UNISWAP_V2 = "UniswapV2"
    QUICKSWAP = "Quickswap"
    SUSHISWAP = "Sushiswap"
    UNISWAP_V3 = "UniswapV3"
    ALGEBRA = "Algebra"
    CURVE = "Curve"


class DEX(str, Enum):
    UNISWAP = "Uniswap"
    QUICKSWAP = "Quickswap"
    SUSHISWAP = "Sushiswap"
    CURVE = "Curve"
    ODOS = "ODOS"
    ZEROX = "0x"


class CandidateRouteType(str, Enum):
    SAME = "SAME"
    REVERSE_RECOMPUTED = "REVERSE_RECOMPUTED"
    MODIFIED = "MODIFIED"
    NONE = "NONE"


class CycleStatus(str, Enum):
    C1_BUILT_C2_EXECUTE = "C1_BUILT_C2_EXECUTE"
    C1_BUILT_C2_NO_OP = "C1_BUILT_C2_NO_OP"


# =============================================================================
# Route Profile
# =============================================================================

class RouteProfile(BaseModel):
    """Token route and DEX information."""
    
    # Token symbols
    token_in_symbol: str
    token_mid_symbol: str
    token_out_symbol: str
    
    # Token addresses
    token_in_address: str
    token_mid_address: str
    token_out_address: str
    
    # Token decimals
    token_in_decimals: int
    token_mid_decimals: int
    token_out_decimals: int
    
    # Pool/DEX info
    buy_pool_address: str
    sell_pool_address: str
    buy_dex: str
    sell_dex: str
    buy_fee_bps: int
    sell_fee_bps: int
    buy_pool_type: str
    sell_pool_type: str
    
    # Protocol details
    protocol_ids: list[str] = Field(default_factory=list)
    router_target_addresses: list[str] = Field(default_factory=list)
    approve_token_per_step: list[str] = Field(default_factory=list)
    output_token_per_step: list[str] = Field(default_factory=list)
    calldata_selector_per_step: list[str] = Field(default_factory=list)
    calldata_len_per_step: list[int] = Field(default_factory=list)
    calldata_hash_per_step: list[str] = Field(default_factory=list)


# =============================================================================
# Reserves State
# =============================================================================

class ReservesState(BaseModel):
    """Pool reserve data."""
    
    # Reserve raw values
    reserve0_raw: str
    reserve1_raw: str
    reserve0_human: str
    reserve1_human: str
    reserve0_usd: str
    reserve1_usd: str
    
    # TVL and usage
    total_tvl_usd: str
    weakest_pool_tvl_usd: str
    pool_usage_fraction: str
    
    # Metadata
    reserve_block: int
    reserve_age_ms: int
    stale_reserve_flag: bool
    reserve_source: str
    
    # State fingerprints
    state_fingerprint_pre_c1: Optional[str] = None
    state_fingerprint_post_c1: Optional[str] = None


# =============================================================================
# Discovery Pricing
# =============================================================================

class DiscoveryPricing(BaseModel):
    """USD-normalized discovery pricing."""
    
    buy_price_usd_per_tokenA: str
    sell_price_usd_per_tokenA: str
    delta_p_raw_usd: str
    raw_spread_bps: str
    raw_profit_usd_at_selected_size: str
    min_spread_bps: str
    spread_sanity_pass: bool


# =============================================================================
# Cost Stack
# =============================================================================

class CostStack(BaseModel):
    """Execution cost breakdown."""
    
    # Flash loan fees
    flash_fee_bps: str
    flash_fee_usd: str
    
    # Gas costs
    gas_limit_estimate: int
    gas_price_gwei: str
    priority_fee_gwei: str
    gas_cost_native: str
    gas_cost_usd: str
    
    # Risk and total
    risk_buffer_usd: str
    c_total_exec_usd: str
    
    # Profit
    gross_profit_usd: str
    net_profit_usd: str
    net_profit_bps: str
    failure_cost_usd: str
    ev_usd: str


# =============================================================================
# Route Envelope
# =============================================================================

class RouteEnvelopeStep(BaseModel):
    """Single step in route envelope."""
    
    protocol: str
    target: str
    approve_token: str
    output_token: str
    call_value: str
    min_amount_in: str
    min_amount_out: str
    expected_amount_out: str
    fee_bps: int
    data_selector: str
    data_len: int
    data_hash: str
    min_out_buffer_bps: int
    token_consistency_pass: bool
    target_allowlist_pass: bool
    data_non_empty_pass: bool
    min_out_sanity_pass: bool


class RouteEnvelope(BaseModel):
    """Full route payload details."""
    
    envelope_version: str
    profit_token: str
    gas_reserve_asset: str
    dex_fee_reserve_asset: str
    step_count: int
    steps: list[RouteEnvelopeStep] = Field(default_factory=list)
    
    # Encoded payload
    encoded_payload_len: int
    encoded_payload_hash: str
    executor_entrypoint: str
    executor_calldata_len: int
    executor_calldata_hash: str
    
    # Safety flags
    would_sign: bool = False
    would_broadcast: bool = False


# =============================================================================
# Decision
# =============================================================================

class Decision(BaseModel):
    """C1/C2 decision data."""
    
    decision: str
    payload_built: bool
    no_op_reason: Optional[str] = None
    c2_never_pre_approved_c1: bool = True
    
    # C2-specific fields
    post_c1_state_fingerprint: Optional[str] = None
    post_c1_buy_price_usd: Optional[str] = None
    post_c1_sell_price_usd: Optional[str] = None
    post_c1_raw_spread_bps: Optional[str] = None
    post_c1_candidate_route_type: Optional[str] = None
    
    # C2 evaluation
    c2_amount_in_usd: Optional[str] = None
    c2_expected_out_usd: Optional[str] = None
    c2_gross_profit_usd: Optional[str] = None
    c2_net_profit_usd: Optional[str] = None
    c2_p_success: Optional[str] = None
    c2_failure_cost_usd: Optional[str] = None
    c2_ev_usd: Optional[str] = None
    c2_decision: Optional[str] = None
    c2_payload_hash: Optional[str] = None


# =============================================================================
# Payload
# =============================================================================

class Payload(BaseModel):
    """Built payload information."""
    
    payload_built: bool
    payload_hash: Optional[str] = None
    calldata: Optional[str] = None
    calldata_len: int = 0
    calldata_hash: Optional[str] = None


# =============================================================================
# EV Probability
# =============================================================================

class EVProbability(BaseModel):
    """Probability and EV calculations."""
    
    p_exec_calibrated: Optional[str] = None
    p_exec_historical: Optional[str] = None
    failure_cost_usd: Optional[str] = None
    ev_usd: Optional[str] = None
    ev_bps: Optional[str] = None


# =============================================================================
# Audit
# =============================================================================

class Audit(BaseModel):
    """Audit and validation results."""
    
    audit_pass: bool
    rpc_health_pass: bool
    pool_tvl_pass: bool
    reserves_pass: bool
    reserves_stale_pass: bool
    reserves_zero_pass: bool
    reserves_unverified_pass: bool
    pool_type_pass: bool
    v3_tick_aware_pass: bool
    spread_sanity_pass: bool
    fork_sim_pass: bool
    payload_sim_pass: bool
    gate_pass: bool


# =============================================================================
# Dashboard
# =============================================================================

class Dashboard(BaseModel):
    """Dashboard display metadata."""
    
    display_ready: bool = True
    show_in_dashboard: bool = True
    card_summary_fields: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Replay
# =============================================================================

class Replay(BaseModel):
    """Replay command information."""
    
    replay_command: Optional[str] = None
    replay_params: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# C1 Aggressor Card
# =============================================================================

class C1AggressorCard(BaseModel):
    """Full C1 Aggressor DNA card."""
    
    # Identity
    card_id: str
    cycle_id: str
    cycle_number: int
    global_cycle_number: int
    block_cycle_number: int
    block_number: int
    block_id: str
    opportunity_id: str
    
    # Role
    strike_role: str = "C1"
    strike_name: str = "Aggressor"
    sequence_index: int = 1
    trigger: str = "scanner_executable_candidate"
    
    # State
    state_basis: str = "pre_c1_state"
    decision: str = "BUILD_PAYLOAD"
    payload_built: bool = True
    shadow_execution_status: str = "NOT_APPLIED"
    
    # Financial
    simulated_net_usd: Optional[str] = None
    realized_net_opportunity_usd: Optional[str] = None
    realized_status: str = "DRY_RUN_NO_BROADCAST"
    
    # Full math
    amount_in_usd: str
    amount_in_raw: str
    amount_in_human: str
    buy_fee_bps: int
    sell_fee_bps: int
    buy_x_eff: str
    buy_R_in: str
    buy_R_out: str
    B_out_1_raw: str
    B_out_1_human: str
    B_out_1_usd: str
    sell_input_equals_buy_output: bool = True
    sell_x_eff: str
    sell_R_in: str
    sell_R_out: str
    A_out_2_raw: str
    A_out_2_human: str
    A_out_2_usd: str
    gross_profit_usd: str
    net_profit_usd: str
    raw_spread_bps: str
    raw_profit_usd_at_selected_size: str
    deterministic_slippage_leg1_bps: str
    deterministic_slippage_leg2_bps: str
    max_leg_slippage_bps: str
    optimal_method: str
    ladder_points_evaluated: int
    selected_size_reason: str
    dPdx_before: str
    dPdx_at_selected: str
    dPdx_after: str
    saturation_detected: bool
    
    # Nested objects
    route_profile: Optional[RouteProfile] = None
    reserves_state: Optional[ReservesState] = None
    discovery_pricing: Optional[DiscoveryPricing] = None
    cost_stack: Optional[CostStack] = None
    route_envelope: Optional[RouteEnvelope] = None
    decision: Optional[Decision] = None
    payload: Optional[Payload] = None
    ev_probability: Optional[EVProbability] = None
    audit: Optional[Audit] = None
    dashboard: Optional[Dashboard] = None
    replay: Optional[Replay] = None
    
    # Timestamp
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# =============================================================================
# C2 Surgeon Card
# =============================================================================

class C2SurgeonCard(BaseModel):
    """Full C2 Surgeon DNA card."""
    
    # Identity
    card_id: str
    cycle_id: str
    cycle_number: int
    global_cycle_number: int
    block_cycle_number: int
    block_number: int
    block_id: str
    opportunity_id: str
    
    # Role
    strike_role: str = "C2"
    strike_name: str = "Surgeon"
    sequence_index: int = 2
    trigger: str  # References C1 card
    
    # State
    state_basis: str = "post_c1_reloaded_state"
    decision: str
    payload_built: bool
    no_op_reason: Optional[str] = None
    c2_never_pre_approved_c1: bool = True
    
    # Financial
    simulated_net_usd: Optional[str] = None
    realized_net_opportunity_usd: Optional[str] = None
    realized_status: str = "DRY_RUN_NO_BROADCAST"
    
    # C2-specific
    post_c1_state_fingerprint: Optional[str] = None
    post_c1_buy_price_usd: Optional[str] = None
    post_c1_sell_price_usd: Optional[str] = None
    post_c1_raw_spread_bps: Optional[str] = None
    post_c1_candidate_route_type: Optional[str] = None
    c2_amount_in_usd: Optional[str] = None
    c2_expected_out_usd: Optional[str] = None
    c2_gross_profit_usd: Optional[str] = None
    c2_net_profit_usd: Optional[str] = None
    c2_p_success: Optional[str] = None
    c2_failure_cost_usd: Optional[str] = None
    c2_ev_usd: Optional[str] = None
    c2_decision: Optional[str] = None
    c2_payload_hash: Optional[str] = None
    
    # Nested objects
    route_profile: Optional[RouteProfile] = None
    reserves_state: Optional[ReservesState] = None
    discovery_pricing: Optional[DiscoveryPricing] = None
    cost_stack: Optional[CostStack] = None
    route_envelope: Optional[RouteEnvelope] = None
    decision: Optional[Decision] = None
    payload: Optional[Payload] = None
    ev_probability: Optional[EVProbability] = None
    audit: Optional[Audit] = None
    dashboard: Optional[Dashboard] = None
    replay: Optional[Replay] = None
    
    # Timestamp
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# =============================================================================
# Cycle Pair
# =============================================================================

class CyclePair(BaseModel):
    """Paired C1/C2 cycle."""
    
    block_number: int
    block_id: str
    block_cycle_number: int
    global_cycle_number: int
    cycle_id: str
    opportunity_id: str
    c1_card_id: str
    c2_card_id: str
    c1_decision: str
    c2_decision: str
    simulated_c1_net_usd: Optional[float] = None
    simulated_c2_net_usd: Optional[float] = None
    simulated_net_usd: Optional[float] = None
    realized_net_opportunity_usd: Optional[float] = None
    realized_status: str = "DRY_RUN_NO_BROADCAST"
    cycle_status: str
    
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# =============================================================================
# Block Cycle
# =============================================================================

class BlockCycle(BaseModel):
    """Block-level aggregate."""
    
    block_number: int
    block_id: str
    block_cycle_count: int
    global_cycle_numbers: list[int]
    opportunity_ids: list[str]
    block_simulated_net_usd: Optional[float] = None
    block_realized_net_opportunity_usd: Optional[float] = None
    realized_status: str = "DRY_RUN_NO_BROADCAST"
    
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# =============================================================================
# DNA Data Schema (top-level)
# =============================================================================

class DNADataSchema(BaseModel):
    """Top-level DNA data schema for serialization."""
    
    version: str = "1.0.0"
    schema_type: str  # "c1_card", "c2_card", "cycle_pair", "block_cycle"
    data: dict[str, Any] = Field(default_factory=dict)
from typing import Any, Dict


def build_c1_card(key, simulated_net_usd: float) -> Dict[str, Any]:
    return {
        'identity': {
            'card_id': f"{key.opportunity_id}_c1",
            'cycle_id': key.cycle_id,
            'global_cycle_number': key.global_cycle_number,
            'block_cycle_number': key.block_cycle_number,
            'block_number': key.block_number,
            'block_id': key.block_id,
            'opportunity_id': key.opportunity_id,
            'strike_role': 'C1',
            'strike_name': 'Aggressor',
            'decision': 'BUILD_PAYLOAD',
        },
        'decision': {'payload_built': True, 'realized_status': 'DRY_RUN_NO_BROADCAST'},
        'math': {'net_profit_usd': simulated_net_usd},
        'payload': {'would_sign': False, 'would_broadcast': False},
    }


def build_c2_card(key, c1_card_id: str, decision: str, simulated_net_usd: float, no_op_reason: str = '') -> Dict[str, Any]:
    card = {
        'identity': {
            'card_id': f"{key.opportunity_id}_c2",
            'cycle_id': key.cycle_id,
            'global_cycle_number': key.global_cycle_number,
            'block_cycle_number': key.block_cycle_number,
            'block_number': key.block_number,
            'block_id': key.block_id,
            'opportunity_id': key.opportunity_id,
            'trigger': c1_card_id,
            'strike_role': 'C2',
            'strike_name': 'Surgeon',
            'decision': decision,
        },
        'decision': {'payload_built': decision == 'EXECUTE', 'realized_status': 'DRY_RUN_NO_BROADCAST'},
        'math': {'net_profit_usd': simulated_net_usd},
        'payload': {'would_sign': False, 'would_broadcast': False},
    }
    if decision == 'NO_OP':
        card['decision']['no_op_reason'] = no_op_reason or 'EV<=0'
    return card
