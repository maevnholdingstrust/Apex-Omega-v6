# APEX_FULL_DRYRUN_DNA_DASHBOARD_PATCH.ps1
# Run from repo root:
# C:\Users\The Urban Genius\Documents\Arbitrage\FINAL BUILD\Apex-Omega-v6

$ErrorActionPreference = "Stop"

$PROMPT = @"
APEX-OMEGA FULL DRY-RUN DNA DASHBOARD PATCH — COMPLETE TO FINISH
NO BROADCAST. NO LIVE TX. NO STRATEGY DRIFT. NO PLACEHOLDERS.

================================================================================
0. OBJECTIVE
================================================================================

Patch the repo so Apex-Omega can run a real-time, dashboard-visible dry-run mode that:

1. Scans real/available candidate routes.
2. Applies hard execution gates.
3. Builds the first 20 executable C1 payload candidates.
4. Performs fork/static simulation where available.
5. Logs each executable C1 as one dual-cycle opportunity.
6. Creates exactly two DNA cards per cycle:
   - one C1 Aggressor card
   - one C2 Surgeon card
7. Reloads or shadow-mutates post-C1 state.
8. Runs exactly one C2 opportunity window per C1 cycle.
9. Logs C2 even when C2 decision is NO_OP.
10. Groups all cycles by block number.
11. Streams all dry-run operations to dashboard in real time.
12. Never signs, broadcasts, or submits live transactions.

This is dry-run operational readiness only.
Do NOT claim mainnet readiness.

================================================================================
1. LOCKED CANONICAL EXECUTION FLOW
================================================================================

The canonical flow is:

scanner
→ gates
→ C1
→ fork sim
→ shadow execution / payload validation only
→ reload or shadow-mutate state
→ C2
→ fork sim
→ shadow execution or NO_OP
→ logs/dashboard

MANDATORY RULES:

1. C1 and C2 are separate strikes but one cycle.
2. C1 = Aggressor.
3. C2 = Surgeon.
4. C2 does not approve C1.
5. C2 does not block C1.
6. C2 does not pre-filter C1.
7. C2 only evaluates after C1 reaches executable payload-build status and C1 shadow execution/state reload has occurred.
8. C2 consumes post-C1 state only.
9. Every executable C1 payload-build opens exactly one C2 evaluation window.
10. C2 output is strictly:
    - EXECUTE
    - NO_OP
11. Reverse route is not a C2 command.
12. If reverse appears, it must be discovered/recomputed from the post-C1 state.
13. Execution layer is mechanical only.
14. Payload builder is mechanical only.
15. Relay/broadcast layer is disabled in this patch.
16. Rejected scanner/C1 candidates are not cycles.
17. Rejected candidates go to dry_run_rejections.jsonl only.

================================================================================
2. HARD NO-BROADCAST SAFETY
================================================================================

Implement:

python/apex_omega_core/safety/dry_run_guard.py

Required objects/functions:

- DryRunBroadcastBlockedError
- assert_dry_run_env()
- assert_no_broadcast(action_name: str)
- assert_no_signing(action_name: str)
- assert_no_relay_submission(action_name: str)
- enforce_no_broadcast_env()

Force these defaults in dry-run:

LIVE_EXECUTION=false
EXECUTION_ENABLED=false
BROADCAST_ENABLED=false
DRY_RUN_DASHBOARD_MODE=true
TX_SIGNING_ENABLED=false
PRIVATE_RELAY_ENABLED=false
TITAN_RELAY_ENABLED=false
FLASHBOTS_RELAY_ENABLED=false

Any attempted call to these must raise DryRunBroadcastBlockedError:

- eth_sendRawTransaction
- eth_sendTransaction
- relay bundle submit
- private relay submit
- Titan relay submit
- Flashbots relay submit
- real wallet signing
- state-changing contract transaction broadcast

Dry-run may build calldata and hashes.
Dry-run may simulate.
Dry-run may not sign or broadcast.

================================================================================
3. FIRST20 RULE
================================================================================

The requested dry-run target is:

first20 = 20 executable C1 cycles

Therefore:

20 executable C1 payload-builds
→ 20 cycles
→ 20 C1 DNA cards
→ 20 C2 DNA cards
→ 40 total DNA card rows

Rejected candidates do NOT count toward first20.

Stop after 20 C1 executable payload-build cycles, regardless of how many C2 cards are NO_OP.

================================================================================
4. BLOCK-LEVEL DNA HIERARCHY
================================================================================

Implement block-level grouping above cycle/opportunity logging.

Hierarchy:

block_number
  → block_cycle_number
    → global_cycle_number
      → cycle_id
        → opportunity_id
          → C1 DNA card
          → C2 DNA card
          → simulated_net_usd
          → realized_net_opportunity_usd

MANDATORY BLOCK RULES:

1. Multiple C1 cycles may exist inside the same block.
2. Each block maintains block_cycle_number starting at 1.
3. System maintains monotonic global_cycle_number across all blocks.
4. cycle_id must include block_number, block_cycle_number, and global_cycle_number.
5. opportunity_id maps one-to-one with global_cycle_number.
6. Every valid opportunity has exactly one C1 card and one C2 card.
7. C2 card exists even when C2 is NO_OP.
8. C2 card references triggering C1 card.
9. Dry-run never reports simulated profit as realized profit.
10. realized_net_opportunity_usd must be null in dry-run.

ID FORMAT:

block_id:
  block_{block_number}

cycle_id:
  block_{block_number}_cycle_{block_cycle_number:06d}_global_{global_cycle_number:06d}

opportunity_id:
  opportunity_{global_cycle_number:06d}

c1_card_id:
  opportunity_{global_cycle_number:06d}_c1

c2_card_id:
  opportunity_{global_cycle_number:06d}_c2

Example:

block_id:
  block_73491288

cycle_id:
  block_73491288_cycle_000001_global_000128

opportunity_id:
  opportunity_000128

c1_card_id:
  opportunity_000128_c1

c2_card_id:
  opportunity_000128_c2

================================================================================
5. MODULES TO ADD
================================================================================

Add or patch:

python/apex_omega_core/dry_run/__init__.py
python/apex_omega_core/dry_run/dna_schema.py
python/apex_omega_core/dry_run/dna_logger.py
python/apex_omega_core/dry_run/block_cycle_index.py
python/apex_omega_core/dry_run/dry_run_orchestrator.py
python/apex_omega_core/dry_run/realtime_bus.py
python/apex_omega_core/dry_run/run_first20_dna_dry_run.py
python/apex_omega_core/safety/dry_run_guard.py

Patch dashboard backend/frontend discovered in repo.

Do not replace working dashboard code blindly.
Inspect existing dashboard first.

================================================================================
6. DNA CARD REQUIRED DATA MODEL
================================================================================

Every DNA card must include these top-level sections:

identity
route_profile
reserves_state
discovery_pricing
math
cost_stack
decision
payload
ev_probability
audit
dashboard
replay

--------------------------------------------------------------------------------
6.1 C1 AGGRESSOR CARD
--------------------------------------------------------------------------------

Required fields:

card_id = opportunity_000128_c1
cycle_id
cycle_number
global_cycle_number
block_cycle_number
block_number
block_id
opportunity_id
strike_role = C1
strike_name = Aggressor
sequence_index = 1
trigger = scanner_executable_candidate
state_basis = pre_c1_state
decision = BUILD_PAYLOAD
payload_built = true
shadow_execution_status = APPLIED_TO_SHADOW_STATE or NOT_APPLIED
simulated_net_usd
realized_net_opportunity_usd = null in dry-run
realized_status = DRY_RUN_NO_BROADCAST

C1 must include full math:

amount_in_usd
amount_in_raw
amount_in_human
buy_fee_bps
sell_fee_bps
buy_x_eff
buy_R_in
buy_R_out
B_out_1_raw
B_out_1_human
B_out_1_usd
sell_input_equals_buy_output = true
sell_x_eff
sell_R_in
sell_R_out
A_out_2_raw
A_out_2_human
A_out_2_usd
gross_profit_usd
net_profit_usd
raw_spread_bps
raw_profit_usd_at_selected_size
deterministic_slippage_leg1_bps
deterministic_slippage_leg2_bps
max_leg_slippage_bps
optimal_method
ladder_points_evaluated
selected_size_reason
dPdx_before
dPdx_at_selected
dPdx_after
saturation_detected

IMPORTANT MATH RULE:

Leg 2 input must equal Leg 1 output:

A_in_2 = A_out_1

Do NOT apply extra slippage to A_out_1 before Leg 2.
AMM output already includes deterministic slippage.

--------------------------------------------------------------------------------
6.2 C2 SURGEON CARD
--------------------------------------------------------------------------------

Required fields:

card_id = opportunity_000128_c2
cycle_id
cycle_number
global_cycle_number
block_cycle_number
block_number
block_id
opportunity_id
strike_role = C2
strike_name = Surgeon
sequence_index = 2
trigger = opportunity_000128_c1
state_basis = post_c1_reloaded_state OR post_c1_shadow_mutated_state
decision = EXECUTE OR NO_OP
payload_built = true OR false
no_op_reason = required when decision is NO_OP
c2_never_pre_approved_c1 = true
simulated_net_usd
realized_net_opportunity_usd = null in dry-run
realized_status = DRY_RUN_NO_BROADCAST

C2 must include:

post_c1_state_fingerprint
post_c1_buy_price_usd
post_c1_sell_price_usd
post_c1_raw_spread_bps
post_c1_candidate_route_type = SAME / REVERSE_RECOMPUTED / MODIFIED / NONE
c2_amount_in_usd
c2_expected_out_usd
c2_gross_profit_usd
c2_net_profit_usd
c2_p_success
c2_failure_cost_usd
c2_ev_usd
c2_decision
c2_payload_hash if built
c2_no_op_reason if NO_OP

C2 EV rule:

EV = net_profit_usd * p_exec_calibrated - (1 - p_exec_calibrated) * failure_cost_usd

C2 only executes if EV > 0 and all execution gates pass.

================================================================================
7. ROUTE PROFILE REQUIREMENTS
================================================================================

Each card must include:

token_in_symbol
token_mid_symbol
token_out_symbol
token_in_address
token_mid_address
token_out_address
token_in_decimals
token_mid_decimals
token_out_decimals

buy_pool_address
sell_pool_address
buy_dex
sell_dex
buy_fee_bps
sell_fee_bps
buy_pool_type
sell_pool_type

protocol_ids
router_target_addresses
approve_token_per_step
output_token_per_step
calldata_selector_per_step
calldata_len_per_step
calldata_hash_per_step

Full calldata may be stored only in local JSONL artifact.
Dashboard should display selector/hash/length unless explicitly configured to show raw calldata.

================================================================================
8. RESERVE / STATE REQUIREMENTS
================================================================================

For each pool:

reserve0_raw
reserve1_raw
reserve0_human
reserve1_human
reserve0_usd
reserve1_usd
total_tvl_usd
weakest_pool_tvl_usd
pool_usage_fraction
reserve_block
reserve_age_ms
stale_reserve_flag
reserve_source
state_fingerprint_pre_c1
state_fingerprint_post_c1

================================================================================
9. DISCOVERY PRICING REQUIREMENTS
================================================================================

Use canonical USD-normalized discovery pricing.

Required fields:

buy_price_usd_per_tokenA
sell_price_usd_per_tokenA
delta_p_raw_usd
raw_spread_bps
raw_profit_usd_at_selected_size
min_spread_bps
spread_sanity_pass

Canonical direction:

positive spread = sell USD price > buy USD price

Do not rely on raw spread sign alone as a decision signal.
Decisions must use executable net edge / EV.

================================================================================
10. COST STACK REQUIREMENTS
================================================================================

Each C1/C2 card must include:

flash_fee_bps
flash_fee_usd
gas_limit_estimate
gas_price_gwei
priority_fee_gwei
gas_cost_native
gas_cost_usd
risk_buffer_usd
c_total_exec_usd
gross_profit_usd
net_profit_usd
net_profit_bps
failure_cost_usd
ev_usd

DEX fees are embedded in AMM outputs.
Do not double-count DEX fees unless separately reported for diagnostics only.

================================================================================
11. PAYLOAD / ROUTE ENVELOPE REQUIREMENTS
================================================================================

Each executable card must include RouteEnvelope detail.

Required fields:

envelope_version
profit_token
gas_reserve_asset
dex_fee_reserve_asset
step_count

For each step:

protocol
target
approve_token
output_token
call_value
min_amount_in
min_amount_out
expected_amount_out
fee_bps
data_selector
data_len
data_hash
min_out_buffer_bps
token_consistency_pass
target_allowlist_pass
data_non_empty_pass
min_out_sanity_pass

Envelope-level:

encoded_payload_len
encoded_payload_hash
executor_entrypoint
executor_calldata_len
executor_calldata_hash
would_sign = false
would_broadcast = false

Hard guards:

1. approve_token must equal actual token being spent.
2. output_token must match expected next-hop token.
3. data must not be empty.
4. target must be allowlisted.
5. min_amount_out must be > 0.
6. min_amount_out must be <= expected_amount_out.
7. step token chain must be continuous.
8. final output must satisfy simulated debt + min profit if applicable.
9. fork/static simulation must match expected output within tolerance.

================================================================================
12. EXECUTION GATES
================================================================================

Before C1 payload build, reject candidate if:

rpc_health_pass = false
pool_tvl_usd < EXECUTABLE_MIN_TVL_USD
reserves missing
reserves stale
reserves zero
reserves unverified
pool type unsupported
V3/Algebra pool lacks tick-aware validation
flash size exceeds weakest_pool_tvl * MAX_POOL_USAGE
spread/profit absurd beyond sanity bounds
fork simulation unavailable where required
payload output mismatch > PAYLOAD_SIM_TOLERANCE_BPS

V3 RULE:

V3 and Algebra pools are valid discovery surfaces but invalid execution candidates until tick-aware quote math, route validation, calldata generation, and fork simulation pass.

Never use V2 x*y=k math on V3/Algebra pools.

================================================================================
13. LOG FILES
================================================================================

Create/write:

logs/dry_run_dna_cards.jsonl
  one row per DNA card
  20 cycles = 40 rows

logs/dry_run_cycle_pairs.jsonl
  one row per paired C1/C2 cycle

logs/dry_run_block_cycles.jsonl
  one row per block summary

logs/dry_run_payload_builds.jsonl
  one row per payload build

logs/dry_run_rejections.jsonl
  rejected candidates only
  not counted as cycles

logs/dry_run_dashboard_events.jsonl
  every event streamed to dashboard

logs/dry_run_summary.json
  final run summary

================================================================================
14. CYCLE PAIR ROW FORMAT
================================================================================

Each dry_run_cycle_pairs.jsonl row must contain:

{
  "block_number": 73491288,
  "block_id": "block_73491288",
  "block_cycle_number": 1,
  "global_cycle_number": 128,
  "cycle_id": "block_73491288_cycle_000001_global_000128",
  "opportunity_id": "opportunity_000128",
  "c1_card_id": "opportunity_000128_c1",
  "c2_card_id": "opportunity_000128_c2",
  "c1_decision": "BUILD_PAYLOAD",
  "c2_decision": "NO_OP",
  "simulated_c1_net_usd": 14.27,
  "simulated_c2_net_usd": 0.0,
  "simulated_net_usd": 14.27,
  "realized_net_opportunity_usd": null,
  "realized_status": "DRY_RUN_NO_BROADCAST",
  "cycle_status": "C1_BUILT_C2_NO_OP"
}

cycle_status values:

C1_BUILT_C2_EXECUTE
C1_BUILT_C2_NO_OP

Rejected C1 candidates are not cycle_status.
They are rejection log records only.

================================================================================
15. BLOCK SUMMARY ROW FORMAT
================================================================================

Each dry_run_block_cycles.jsonl row must contain:

{
  "block_number": 73491288,
  "block_id": "block_73491288",
  "block_cycle_count": 3,
  "global_cycle_numbers": [128, 129, 130],
  "opportunity_ids": [
    "opportunity_000128",
    "opportunity_000129",
    "opportunity_000130"
  ],
  "block_simulated_net_usd": 42.81,
  "block_realized_net_opportunity_usd": null,
  "realized_status": "DRY_RUN_NO_BROADCAST"
}

Metric definitions:

simulated_net_usd =
  simulated_c1_net_usd + simulated_c2_net_usd

block_simulated_net_usd =
  sum(simulated_net_usd for all cycles in block)

realized_net_opportunity_usd =
  actual post-settlement realized PnL only

In dry-run:
  realized_net_opportunity_usd = null
  block_realized_net_opportunity_usd = null
  realized_status = DRY_RUN_NO_BROADCAST

Allowed realized_status values:

DRY_RUN_NO_BROADCAST
LIVE_REALIZED
LIVE_REVERTED
LIVE_DROPPED
LIVE_UNKNOWN

================================================================================
16. REAL-TIME DASHBOARD REQUIREMENTS
================================================================================

Patch existing dashboard/server structure.

Backend API:

GET  /api/dry-run/status
POST /api/dry-run/start?limit=20
POST /api/dry-run/stop
GET  /api/dry-run/dna-cards
GET  /api/dry-run/dna-cards/<cycle_id>
GET  /api/dry-run/cycle-pairs
GET  /api/dry-run/block-cycles
GET  /api/dry-run/payloads
GET  /api/dry-run/rejections
GET  /api/dry-run/summary
GET  /api/dry-run/events/stream

Use SSE if WebSocket infra is not already present.
Use WebSocket only if repo already has WebSocket infrastructure.

Dashboard display hierarchy:

Block #73491288
  Cycle #1 | Opportunity #000128
    C1 Aggressor Card
    C2 Surgeon Card
    Simulated Net
    Realized Net: DRY RUN / NO BROADCAST

  Cycle #2 | Opportunity #000129
    C1 Aggressor Card
    C2 Surgeon Card
    Simulated Net
    Realized Net: DRY RUN / NO BROADCAST

Dashboard filters:

Show All
C1 Only
C2 Only
C2 NO_OP
C2 EXECUTE
Block
Opportunity
Positive simulated net
Failed audit
Payload built
Fork sim pass
Fork sim fail

Dashboard card summary fields:

block_number
block_cycle_number
global_cycle_number
opportunity_id
cycle_id
c1_decision
c2_decision
buy_dex
sell_dex
route
selected_size_usd
raw_spread_bps
gross_profit_usd
net_profit_usd
ev_usd
p_exec_calibrated
fork_sim_status
payload_hash
audit_pass
realized_status
NO_BROADCAST badge

Clicking a card opens:

full C1 math
full C2 post-C1 recompute
reserves before/after
RouteEnvelope fields
calldata selectors/hashes/lengths
minOut cascade
EV/probability model
gate results
rejection/audit details
replay command

================================================================================
17. REAL-TIME EVENT BUS
================================================================================

Every dashboard event must also be written to:

logs/dry_run_dashboard_events.jsonl

Required events:

DRY_RUN_STARTED
CANDIDATE_SCANNED
CANDIDATE_REJECTED
C1_PAYLOAD_BUILT
C1_FORK_SIM_PASS
C1_FORK_SIM_FAIL
C1_SHADOW_STATE_APPLIED
C2_EVALUATION_STARTED
C2_PAYLOAD_BUILT
C2_NO_OP
C2_FORK_SIM_PASS
C2_FORK_SIM_FAIL
DNA_CARD_CREATED
CYCLE_PAIR_CREATED
BLOCK_SUMMARY_UPDATED
DRY_RUN_DONE
DRY_RUN_ABORTED
BROADCAST_ATTEMPT_BLOCKED

================================================================================
18. CLI REQUIREMENTS
================================================================================

Add command:

python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 20 --dashboard-stream --no-broadcast

Optional patch to existing dry_run.py:

python python/dry_run.py --first20-dna --dashboard-stream --no-broadcast

CLI must:

1. Force dry-run env.
2. Assert no-broadcast.
3. Run until 20 executable C1 cycles or candidate exhaustion.
4. Emit 40 DNA cards for 20 cycles.
5. Write all logs.
6. Stream dashboard events.
7. Print final summary.
8. Never sign.
9. Never broadcast.

================================================================================
19. TESTS REQUIRED
================================================================================

Add/update tests:

test_cycle_creates_c1_and_c2_cards
test_c2_card_exists_when_no_op
test_c2_card_references_c1_card
test_multiple_cycles_same_block_get_incrementing_block_cycle_numbers
test_global_cycle_number_monotonic_across_blocks
test_cycle_id_contains_block_and_global_cycle
test_block_summary_sums_simulated_net_usd
test_dry_run_realized_net_is_null
test_first20_means_20_c1_cycles_and_40_cards
test_rejected_candidates_do_not_create_cycles
test_c2_card_not_created_before_post_c1_reload_or_shadow_mutation
test_c2_consumes_post_c1_state_only
test_c2_output_is_execute_or_no_op_only
test_broadcast_attempt_blocked_in_dry_run
test_payload_card_contains_route_envelope_fields_and_hashes
test_dashboard_groups_by_block_then_cycle
test_dashboard_event_stream_emits_required_events
test_logs_are_valid_jsonl
test_v3_candidate_rejected_without_tick_aware_validation
test_dust_pool_rejected
test_missing_reserves_rejected
test_unsafe_flash_size_rejected
test_payload_sim_mismatch_rejected

================================================================================
20. VALIDATION COMMANDS
================================================================================

After patching, run:

python -m compileall python/apex_omega_core
pytest python/apex_omega_core/tests -q

If dashboard frontend tests exist, run them.
If package.json exists, run:

npm test
npm run build

If not feasible, report why.

================================================================================
21. LIVE EXECUTION BLOCKERS TO REPORT
================================================================================

At the end, list remaining live execution blockers:

1. Private Polygon RPC health and authorization.
2. Fork simulation provider readiness.
3. V3/Algebra tick-aware quote math and calldata.
4. Router calldata generators for QuickSwap/Sushi/UniswapV3/Algebra/ODOS/0x as applicable.
5. RouteEnvelope fork simulation parity.
6. Contract ABI/address verification.
7. Nonce/lane lock service.
8. Gas strategy calibration.
9. p_exec calibration from observed inclusion/revert/latency.
10. Redis durability and reconnect/backpressure behavior.
11. Key custody / signing safety.
12. Relay/bundle integration.
13. Kill switch / loss limits.
14. Token decimal normalization.
15. Mainnet shadow-run proof over real blocks.
16. Legal/financial/operator risk acceptance.

================================================================================
22. CODEX OUTPUT REQUIRED
================================================================================

Return:

1. Files changed.
2. Modules added.
3. Dashboard endpoints added.
4. Dashboard components/pages added.
5. Logs added.
6. Tests added.
7. Commands to run dry-run dashboard mode.
8. Commands to inspect logs.
9. Proof no broadcast path is reachable.
10. Remaining live execution blockers.
11. Any unresolved blockers.

Do NOT claim mainnet readiness.
Do NOT enable live execution.
Do NOT broadcast.
"@

$PROMPT_PATH = ".\APEX_FULL_DRYRUN_DNA_DASHBOARD_PROMPT.md"
Set-Content -Path $PROMPT_PATH -Value $PROMPT -Encoding UTF8

Write-Host ""
Write-Host "Created full Codex prompt:"
Write-Host $PROMPT_PATH
Write-Host ""

# Force dry-run/no-broadcast environment in current shell
$env:LIVE_EXECUTION = "false"
$env:EXECUTION_ENABLED = "false"
$env:BROADCAST_ENABLED = "false"
$env:DRY_RUN_DASHBOARD_MODE = "true"
$env:TX_SIGNING_ENABLED = "false"
$env:PRIVATE_RELAY_ENABLED = "false"
$env:TITAN_RELAY_ENABLED = "false"
$env:FLASHBOTS_RELAY_ENABLED = "false"
$env:APEX_DNA_DRY_RUN_LIMIT = "20"

Write-Host "Dry-run environment forced:"
Write-Host "LIVE_EXECUTION=$env:LIVE_EXECUTION"
Write-Host "EXECUTION_ENABLED=$env:EXECUTION_ENABLED"
Write-Host "BROADCAST_ENABLED=$env:BROADCAST_ENABLED"
Write-Host "DRY_RUN_DASHBOARD_MODE=$env:DRY_RUN_DASHBOARD_MODE"
Write-Host "TX_SIGNING_ENABLED=$env:TX_SIGNING_ENABLED"
Write-Host "PRIVATE_RELAY_ENABLED=$env:PRIVATE_RELAY_ENABLED"
Write-Host "TITAN_RELAY_ENABLED=$env:TITAN_RELAY_ENABLED"
Write-Host "FLASHBOTS_RELAY_ENABLED=$env:FLASHBOTS_RELAY_ENABLED"
Write-Host ""

if (Get-Command codex -ErrorAction SilentlyContinue) {
    Write-Host "Running Codex full-auto patch..."
    codex exec --full-auto --sandbox workspace-write --prompt-file $PROMPT_PATH
}
else {
    Write-Host "Codex CLI not found. Prompt file is ready."
    Write-Host "Opening in Notepad..."
    notepad $PROMPT_PATH
}

Write-Host ""
Write-Host "After Codex finishes, run:"
Write-Host "python -m compileall python/apex_omega_core"
Write-Host "pytest python/apex_omega_core/tests -q"
Write-Host "python -m apex_omega_core.dry_run.run_first20_dna_dry_run --limit 20 --dashboard-stream --no-broadcast"
Write-Host ""
Write-Host "Dashboard:"
Write-Host "bash ./start.sh --dashboard-only"
Write-Host ""
Write-Host "Expected dry-run output:"
Write-Host "20 executable C1 cycles"
Write-Host "40 DNA cards"
Write-Host "20 C1 cards"
Write-Host "20 C2 cards"
Write-Host "realized_net_opportunity_usd = null"
Write-Host "NO BROADCAST"
