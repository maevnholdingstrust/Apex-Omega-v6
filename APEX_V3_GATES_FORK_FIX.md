COMPLETE PATCH NOW.

GOALS:
1. Complete all V3 tick math configurations.
2. Separate V2 math/builders from V3 math/builders.
3. Build hard execution gate layer.
4. Build fork validation harness.
5. Ensure fork simulation output matches RouteEnvelope payload output.
6. Calibrate p_exec against observed inclusion rate.

IMPLEMENT:

V3 MODULES:
- v3_pool_state.py
- v3_tick_math.py
- v3_swap_math.py
- v3_quoter.py
- v3_route_validator.py
- uniswap_v3_router.py
- algebra_router.py

V3 REQUIREMENTS:
- sqrtPriceX96 support
- tick support
- liquidity support
- tickSpacing support
- fee tier support
- token0/token1 direction correctness
- decimals correctness
- no V2 x*y=k math on V3/Algebra pools
- V3 execution disabled unless tick-aware quote + calldata + fork simulation pass

HARD EXECUTION GATES:
Reject before C1/C2/payload if:
- RPC unhealthy
- TVL below EXECUTABLE_MIN_TVL_USD
- reserves missing/zero/stale/unverified
- unsupported pool type
- dust pool
- unsafe flash size
- absurd spread/profit
- V3 candidate lacks tick validation
- route calldata missing
- fork sim fails
- payload output mismatch > PAYLOAD_SIM_TOLERANCE_BPS

FORK VALIDATION HARNESS:
- Build RouteEnvelope
- Encode calldata
- Run fork/static simulation against executor
- Compare expected_out, min_out, final_profit
- Reject mismatch
- Log fork_validation_result

P_EXEC CALIBRATION:
- Track inclusion_rate
- Track relay success/failure
- Track latency_to_block
- Track revert_rate
- Track state_prediction_error
- Update calibrated p_exec:
  p_exec = blend(model_p_exec, observed_inclusion_rate)

CANON:
Only C1 and C2 decide.
Execution layer is mechanical.
Punch 2 is a new recompute from new state.

TESTS:
Add tests for V3 separation, V3 rejection without tick math, dust rejection, invalid reserve rejection, unsafe flash rejection, fork mismatch rejection, p_exec calibration, and state prediction accuracy logging.

Return files changed and blockers only.
