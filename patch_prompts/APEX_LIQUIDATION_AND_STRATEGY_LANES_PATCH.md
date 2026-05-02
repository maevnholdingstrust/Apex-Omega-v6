# APEX-OMEGA PATCH  LIQUIDATION + STRATEGY LANES ADDITION
# NO PLACEHOLDERS. NO CANON DRIFT. NO LIVE BROADCAST ENABLEMENT.

You are patching the Apex-Omega-v6 repo.

The goal is to add liquidation hunting and multi-strategy opportunity lanes while preserving the locked Apex execution canon:

scanner
 gates
 C1
 fork sim
 execute/shadow C1
 reload / mutate post-C1 state
 C2
 fork sim
 execute or NO_OP

MANDATORY CANON RULES:
1. Only C1 and C2 are decision authorities.
2. Execution layer is mechanical only.
3. C2 must not approve, block, or pre-filter C1.
4. C2 only evaluates after C1 execution/shadow execution and post-C1 state reload.
5. Punch 2 is always a new recompute from new state.
6. C2 output is only:
   - EXECUTE
   - NO_OP
7. Liquidation is a parallel opportunity lane, not a replacement for cross-DEX arbitrage.
8. Do not merge liquidation math into AMM arbitrage math.
9. Do not enable live broadcasting.
10. Keep EXECUTION_ENABLED=false unless already explicitly configured otherwise by user.

============================================================
PHASE 1  STRATEGY LANE ENUM / OPPORTUNITY ENVELOPE
============================================================

Add or update a shared opportunity envelope model.

Create or patch:

python/apex_omega_core/core/opportunity_envelope.py

Required structures:

OpportunityKind:
- CROSS_DEX_ARB
- LIQUIDATION
- AGGREGATOR_OPTIMIZATION
- RATE_ARB
- SELF_LIQUIDATION
- COLLATERAL_SWAP

RealizedStatus:
- DRY_RUN_NO_BROADCAST
- LIVE_REALIZED
- LIVE_REVERTED
- LIVE_DROPPED
- LIVE_UNKNOWN

C1Decision:
- BUILD_PAYLOAD
- REJECT

C2Decision:
- EXECUTE
- NO_OP

OpportunityEnvelope fields:
- opportunity_id: str
- kind: OpportunityKind
- chain_id: int
- block_number: int | None
- discovery_source: str
- candidate: dict
- deterministic_math: dict
- execution_plan: dict
- fork_sim: dict | None
- c1_decision: str | None
- c2_decision: str | None
- simulated_net_usd: float | None
- realized_net_usd: float | None
- realized_status: str
- created_at_utc: str

Rules:
- realized_net_usd must be null for dry-run/no-broadcast.
- simulated profit must never be labeled as realized profit.
- All strategies must normalize into OpportunityEnvelope before DNA logging.

============================================================
PHASE 2  LIQUIDATION MODULE LAYOUT
============================================================

Create:

python/apex_omega_core/liquidation/__init__.py
python/apex_omega_core/liquidation/liquidation_candidate.py
python/apex_omega_core/liquidation/liquidation_scanner.py
python/apex_omega_core/liquidation/liquidation_math.py
python/apex_omega_core/liquidation/liquidation_exit_router.py
python/apex_omega_core/liquidation/liquidation_gates.py
python/apex_omega_core/liquidation/liquidation_fork_sim.py
python/apex_omega_core/liquidation/liquidation_dna.py
python/apex_omega_core/liquidation/liquidation_service.py

liquidation_candidate.py:
Define LiquidationCandidate:
- protocol: str
- borrower: str
- debt_asset: str
- collateral_asset: str
- health_factor: float
- max_repay_amount_raw: int
- max_repay_amount_usd: float
- liquidation_bonus_bps: int
- collateral_price_usd: float | None
- debt_price_usd: float | None
- block_number: int | None
- source: str
- metadata: dict

liquidation_math.py:
Define LiquidationMathResult:
- repay_amount_usd
- collateral_received_usd
- liquidation_bonus_usd
- flash_fee_usd
- collateral_exit_slippage_usd
- gas_usd
- relay_tip_usd
- risk_buffer_usd
- net_profit_usd
- break_even_usd
- is_profitable

Implement:
calculate_liquidation_profit(candidate, repay_amount_usd, flash_fee_bps, gas_usd, relay_tip_usd, exit_slippage_usd, risk_buffer_usd)

Formula:
collateral_received_usd =
    repay_amount_usd * (1 + liquidation_bonus_bps / 10000)

liquidation_bonus_usd =
    collateral_received_usd - repay_amount_usd

net_profit_usd =
    collateral_received_usd
  - repay_amount_usd
  - flash_fee_usd
  - collateral_exit_slippage_usd
  - gas_usd
  - relay_tip_usd
  - risk_buffer_usd

Rules:
- Reject if health_factor >= 1.
- Reject if repay_amount_usd <= 0.
- Reject if liquidation_bonus_bps <= 0.
- is_profitable = net_profit_usd > 0.

liquidation_exit_router.py:
Add exit route abstraction:
- collateral_asset
- debt_asset
- route_steps
- expected_out_usd
- min_out_usd
- slippage_bps
- pool_families
- route_supported

Rules:
- Collateral exit must be simulated.
- Do not accept a liquidation unless collateral can be swapped back into debt asset or approved repayment/profit asset.
- Unsupported pool family rejects candidate.
- V3/Algebra requires tick-aware path or quoter/fork-sim proof.
- Do not use V2 CPMM math on V3/Algebra.

liquidation_gates.py:
Implement validate_liquidation_candidate(candidate, math_result, exit_route, config) -> GateResult.

Reject if:
- borrower missing
- debt_asset missing
- collateral_asset missing
- health_factor >= 1.0
- debt asset unsupported
- collateral asset unsupported
- max repay <= 0
- liquidation bonus <= 0
- collateral exit route missing
- collateral exit slippage > MAX_LIQUIDATION_EXIT_SLIPPAGE_BPS
- collateral exit pool TVL < EXECUTABLE_MIN_TVL_USD
- net_profit_usd <= MIN_LIQUIDATION_PROFIT_USD
- EV <= 0 if EV is available
- fork sim failed
- expected collateral received differs from fork result beyond PAYLOAD_SIM_TOLERANCE_BPS

liquidation_fork_sim.py:
Add interface for exact dry-run/static/fork simulation.

Required result:
- ok: bool
- revert_reason: str | None
- expected_collateral_received_raw
- expected_debt_asset_out_raw
- expected_profit_usd
- actual_or_simulated_profit_usd
- mismatch_bps
- block_number
- simulation_provider
- metadata

No placeholder positive pass.
If simulation backend is unavailable, return ok=false with reason SIM_BACKEND_UNAVAILABLE.

liquidation_service.py:
Pipeline:
scan candidates
 gate health factor
 compute liquidation math
 build collateral exit route
 hard gates
 create OpportunityEnvelope(kind=LIQUIDATION)
 fork sim
 C1 decision BUILD_PAYLOAD or REJECT
 write DNA card
 shadow execute or no-broadcast log
 reload state
 C2 EV check
 C2 EXECUTE or NO_OP
 write cycle pair

Do not broadcast.

============================================================
PHASE 3  AAVE V3 / PROTOCOL ADAPTER INTERFACE
============================================================

Add a generic protocol adapter layer.

Create:

python/apex_omega_core/liquidation/protocols/__init__.py
python/apex_omega_core/liquidation/protocols/aave_v3_adapter.py

aave_v3_adapter.py:
Implement interface skeletons with real fail-closed behavior:
- get_user_account_data(user)
- get_liquidatable_users()
- get_reserve_config(asset)
- get_liquidation_bonus_bps(collateral_asset)
- get_close_factor(candidate)
- build_liquidation_call(candidate, repay_amount_raw)

Rules:
- If live protocol indexing is not configured, do not fabricate users.
- Return empty list or fail-closed status.
- No fake borrower addresses.
- No fake health factors.
- Scanner can read from configured subgraph/cache/RPC only if configured.

============================================================
PHASE 4  PAYLOAD BUILDER FOR LIQUIDATION
============================================================

Create:

python/apex_omega_core/execution/liquidation_payload_builder.py

Define LiquidationPayload:
- protocol
- borrower
- debt_asset
- collateral_asset
- repay_amount_raw
- min_profit_raw
- exit_route_payload
- encoded_call_data
- payload_hash

Build target call shape:

initLiquidationFlash(
    address debtAsset,
    uint256 debtAmount,
    address collateralAsset,
    address borrower,
    uint256 minProfit,
    bytes exitRoutePayload
)

Rules:
- Payload builder does not decide profitability.
- Payload builder only encodes C1/C2-approved intent.
- Reject missing or empty calldata.
- Reject zero repay amount.
- Reject zero borrower.
- Reject unsupported protocol.
- Require fork sim before executable status.

============================================================
PHASE 5  CONTRACT STUB / INTERFACE
============================================================

Create or patch:

contracts/FlashLiquidator.sol

The contract must be mechanical only.

Required high-level flow:
1. initLiquidationFlash(...)
2. flashloan debtAsset
3. in callback:
   - approve debtAsset to lending protocol
   - call liquidationCall(collateralAsset, debtAsset, borrower, debtAmount, receiveAToken=false)
   - receive collateral
   - execute pre-approved exit route collateral  debtAsset/profit asset
   - repay flashloan + premium
   - require final balance >= minProfit
   - transfer profit

Rules:
- No route choosing on-chain.
- No profitability decisions on-chain except final require/minProfit enforcement.
- No owner-drain pattern beyond safe recovery functions if existing project style allows.
- Use SafeERC20.
- Add reentrancy/callback guard.
- Keep this as compile-safe contract if Foundry/Hardhat exists.
- If project contract framework is unknown, add interface and documentation rather than breaking compile.

============================================================
PHASE 6  C2 EV / P_EXEC SUPPORT FOR STRATEGY LANES
============================================================

Patch existing C2 EV gate to accept OpportunityKind.

C2 inputs for liquidation:
- net_profit_usd
- p_success
- failure_cost_usd
- gas_usd
- exit_slippage_bps
- health_factor_distance
- competitor_density
- relay_success_rate
- historical_liquidation_success_rate
- recent_revert_rate
- fork_sim_ok

Formula:
EV = p_success * net_profit_usd - (1 - p_success) * failure_cost_usd

Rules:
- If EV <= 0: C2 NO_OP
- If p_success < MIN_P_EXEC: C2 NO_OP
- If fork_sim_ok is false: C2 NO_OP
- C2 may not change C1 math.
- C2 may only EXECUTE or NO_OP.

============================================================
PHASE 7  DNA / DASHBOARD LOGGING
============================================================

Patch DNA logs to include strategy kind.

Required logs:
logs/dry_run_dna_cards.jsonl
logs/dry_run_cycle_pairs.jsonl
logs/dry_run_block_cycles.jsonl

For liquidation C1 DNA card include:
- opportunity_id
- kind = LIQUIDATION
- protocol
- borrower
- health_factor
- debt_asset
- collateral_asset
- repay_amount_usd
- collateral_received_usd
- liquidation_bonus_usd
- flash_fee_usd
- collateral_exit_slippage_usd
- gas_usd
- risk_buffer_usd
- net_profit_usd
- fork_sim_status
- c1_decision

For liquidation C2 DNA card include:
- c2_decision
- EV
- p_success
- failure_cost_usd
- no_op_reason if NO_OP
- references triggering C1 card

Dashboard/API:
Patch endpoints to preserve existing behavior and add strategy fields:
- /api/dry-run/dna-cards
- /api/dry-run/cycle-pairs
- /api/dry-run/block-cycles
- /api/dry-run/summary

Summary should include:
- total opportunities by kind
- total liquidation candidates
- liquidation accepted count
- liquidation rejected count
- liquidation simulated_net_usd
- realized_net remains null in dry-run

============================================================
PHASE 8  CONFIG
============================================================

Patch or document .env.example only. Do not overwrite real .env secrets.

Add:

LIQUIDATION_ENABLED=false
LIQUIDATION_DRY_RUN_ONLY=true
AAVE_V3_LIQUIDATION_ENABLED=true
MIN_LIQUIDATION_PROFIT_USD=10
MAX_LIQUIDATION_EXIT_SLIPPAGE_BPS=100
MIN_LIQUIDATION_HEALTH_FACTOR=0.0
MAX_LIQUIDATION_HEALTH_FACTOR=0.9999
LIQUIDATION_FLASH_FEE_BPS=9
LIQUIDATION_RISK_BUFFER_BPS=20
LIQUIDATION_MAX_REPAY_USD=100000
LIQUIDATION_MIN_REPAY_USD=100
LIQUIDATION_REQUIRE_FORK_SIM=true

EXECUTION_ENABLED=false

Rules:
- Do not enable live.
- Do not add secrets.
- Do not expose API keys.

============================================================
PHASE 9  TESTS REQUIRED
============================================================

Add tests:

tests/test_opportunity_envelope.py
- test_cross_dex_and_liquidation_share_envelope
- test_dry_run_realized_net_is_null
- test_strategy_kind_required

tests/test_liquidation_math.py
- test_liquidation_profit_positive_when_bonus_covers_costs
- test_liquidation_profit_negative_when_exit_slippage_too_high
- test_health_factor_above_one_rejected
- test_zero_repay_rejected

tests/test_liquidation_gates.py
- test_missing_exit_route_rejected
- test_unsupported_collateral_rejected
- test_exit_slippage_over_limit_rejected
- test_fork_sim_required
- test_min_profit_required

tests/test_liquidation_canon_flow.py
- test_liquidation_c1_before_c2
- test_c2_does_not_approve_c1
- test_c2_consumes_post_c1_state_only
- test_c2_outputs_only_execute_or_noop

tests/test_liquidation_dna.py
- test_liquidation_c1_card_written
- test_liquidation_c2_card_written_even_when_noop
- test_liquidation_cycle_pair_references_c1_and_c2
- test_summary_counts_liquidation_kind

tests/test_execution_payload_safety.py
- test_liquidation_payload_rejects_zero_repay
- test_liquidation_payload_rejects_empty_exit_route
- test_payload_builder_does_not_decide_profitability

All tests must pass with:
python -m pytest

Do not remove existing tests.

============================================================
PHASE 10  DOCS
============================================================

Create:

docs/LIQUIDATION_LANE.md
docs/STRATEGY_LANES.md

Must explain:
- liquidation is parallel lane
- C1 deterministic liquidation math
- C2 EV/no-op decision
- execution mechanical only
- no broadcast in dry-run
- collateral exit simulation is mandatory
- liquidation profit is invalid until collateral exit is simulated
- flashloan removes principal need but not gas/execution risk

============================================================
PHASE 11  FINAL REPORT
============================================================

After patching, return:

1. Files changed
2. New modules added
3. Tests added
4. Canon rules preserved
5. Whether tests pass
6. Any unresolved blockers
7. Confirmation that live execution remains disabled

Do not claim live profitability.
Do not claim mainnet readiness unless real fork simulation and payload execution tests pass.
