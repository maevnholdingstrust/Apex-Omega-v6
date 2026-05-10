APEX-OMEGA REQUIRED FIX PATCH — NO PLACEHOLDERS

Patch the repo to enforce these requirements:

1. V3 MATH SEPARATION
- Never use V2 x*y=k math on V3/Algebra pools.
- Add/verify separate modules:
  - v3_pool_state.py
  - v3_tick_math.py
  - v3_swap_math.py
  - v3_quoter.py
  - v3_route_validator.py
  - uniswap_v3_router.py
  - algebra_router.py
- V2 route builder and V3 route builder must be separate.
- V3 candidates may be discovered, but cannot execute unless tick-aware quote + calldata + fork sim pass.

2. HARD EXECUTION GATES
Before C1/C2 or payload build, reject candidate if:
- RPC health check fails
- pool TVL < EXECUTABLE_MIN_TVL_USD
- reserves are missing, stale, zero, or unverified
- pool type unsupported
- V3 pool lacks tick-aware validation
- flash size exceeds weakest_pool_tvl * MAX_POOL_USAGE
- spread/profit is absurd beyond configured sanity bounds
- fork simulation fails
- payload output != expected execution output within tolerance

3. EXECUTION PROBABILITY / LATENCY / FILTERING / STATE ACCURACY
Patch p_exec model to use:
- actual historical inclusion_rate
- relay success/failure
- bundle latency to target block
- gas percentile rank
- mempool density
- route complexity
- recent revert rate
- prediction error between simulated and actual output
Calibrate p_exec after each execution attempt.

4. STATE PREDICTION ACCURACY
Add post-block audit:
- predicted reserves vs actual reserves
- expected_out vs actual_out
- expected_profit vs realized_profit
- update rolling prediction_error metrics
- auto-tighten or loosen risk buffer based on error.

5. FORK SIMULATION BEFORE EXECUTION
Every executable candidate must run:
- build RouteEnvelope
- encode calldata
- static/fork simulate exact executor call
- compare simulation output to C1/C2 expected output
- reject if mismatch > PAYLOAD_SIM_TOLERANCE_BPS

6. ALIGN P_EXEC WITH INCLUSION RATE
Add rolling calibration:
p_exec_calibrated = blend(model_p_exec, observed_inclusion_rate, calibration_weight)

7. TESTS REQUIRED
Add or update tests for:
- V2 candidate cannot use V3 math
- V3 candidate rejected without tick-aware validation
- dust pool rejected
- invalid reserves rejected
- unsafe flash size rejected
- payload sim mismatch rejected
- p_exec updates after inclusion/revert events
- state prediction error logged after execution

8. DO NOT CHANGE CANON
Only C1 and C2 are decision authorities.
Execution layer is mechanical only.
Punch 2 is always a new recompute from new state.

Return:
- files changed
- exact gates added
- exact tests added
- any unresolved blockers
