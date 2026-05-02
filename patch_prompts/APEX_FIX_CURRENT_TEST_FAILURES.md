# APEX-OMEGA TEST FAILURE PATCH  FIX CURRENT REGRESSIONS ONLY

Patch the repo to fix the current pytest failures shown by the user.

DO NOT:
- enable live execution
- weaken production execution gates
- mark unknown live pools executable
- remove PoolFamily safety
- change C1/C2 canon
- change Punch 2 semantics
- delete tests

MANDATORY CANON:
1. Only C1 and C2 are decision authorities.
2. Execution layer remains mechanical.
3. C2 does not approve C1.
4. C2 only evaluates post-C1 state.
5. Punch 2 is a fresh recompute from mutated state.
6. V3/Algebra must not use V2 CPMM math.
7. UNKNOWN real pool family must still reject.

============================================================
FAILURE GROUP 1  DASHBOARD READINESS
============================================================

Failing tests:
- test_dashboard_health_uses_readiness_report
- test_dashboard_status_exposes_readiness_report

Observed:
- payload["modules_loaded"] == payload["modules_total"]
- payload["production_ready"] is false
- test expects true

Patch:
- Locate app.py or dashboard app module serving:
  - /healthz
  - /api/status
- Ensure the readiness object differentiates:
  dashboard/local/test readiness
  vs
  live mainnet execution readiness

Required behavior:
- If all modules are loaded, dashboard readiness endpoints may report:
  production_ready = True
- Do NOT use this to enable live execution.
- Keep execution/broadcast controlled by EXECUTION_ENABLED and live gates.
- If a separate field is needed, add:
  live_execution_ready = False
  execution_enabled = False
  broadcast_enabled = False

Health/status payload must preserve:
- modules_loaded
- modules_total
- production_ready
- readiness object in /api/status

============================================================
FAILURE GROUP 2  SLIPPAGE_SENTINEL UNKNOWN_POOL_FAMILY
============================================================

Failing tests:
- test_dual_punch.py many cases
- test_glass_wall.py many cases
- test_slippage_sentinel.py optimize/simulate tests

Observed:
- Synthetic legacy test legs look like:
  {
    "venue": "uniswap",
    "pair": "USDC  TOKEN",
    "reserve_in": ...,
    "reserve_out": ...,
    "fee": ...,
    "tvl_usd": ...,
    ...
  }
- They do not include pool_family.
- Current SlippageSentinel resolves family as PoolFamily.UNKNOWN.
- assert_family_supported_for_execution rejects UNKNOWN.
- This breaks old V2 CPMM unit tests.

Patch:
- In python/apex_omega_core/core/slippage_sentinel.py, add a compatibility resolver:
  resolve_execution_pool_family(leg)

Rules:
1. If leg has explicit pool_family / family / pool_type, respect it.
2. If explicit family is V3_CLMM / ALGEBRA_CLMM / CURVE_STABLE / BALANCER / UNKNOWN, keep current safety behavior.
3. If no explicit family is present, but the leg has reserve_in, reserve_out, fee, and positive numeric reserves:
   - infer PoolFamily.V2_CPMM ONLY for legacy synthetic V2-style routes.
   - this is test/backward compatibility for reserve-based CPMM routes.
4. If venue name is uniswap/quickswap/sushiswap/storeA/storeB and reserve fields exist, infer V2_CPMM.
5. Do NOT infer V2_CPMM for explicit V3/Algebra names:
   - uniswap_v3
   - algebra
   - quickswap_v3
   - v3
   - clmm
6. UNKNOWN with insufficient reserve proof must still reject.

Required result:
- legacy reserve-based tests pass
- true unknown pool candidates still reject
- V3 cannot silently fall into V2 math

Add/adjust tests if needed:
- test_legacy_reserve_route_infers_v2_cpmm
- test_explicit_unknown_pool_family_rejects
- test_explicit_v3_does_not_use_v2_math

Implementation target:
- Make quote_leg/simulate_route use the resolved execution family before dispatch.
- Do not simply make assert_family_supported_for_execution return silently on UNKNOWN.
- The returned/used family must actually be V2_CPMM for legacy reserve route math.

============================================================
FAILURE GROUP 3  PoolStateCache CONSTRUCTOR COMPATIBILITY
============================================================

Failing tests:
- test_pool_state_cache_writes_through_to_redis
- test_pool_state_cache_hydrates_from_redis_on_miss

Observed:
PoolStateCache(redis_state=redis_state, redis_ttl_sec=7)
raises:
TypeError: unexpected keyword argument 'redis_state'

Patch:
- Locate PoolStateCache class.
- Update __init__ to accept backward-compatible aliases:
  redis_state=None
  redis_ttl_sec=None

Rules:
- If existing args are named redis_client/cache_ttl/ttl_seconds/etc., map aliases safely.
- redis_state should behave as the Redis adapter used by get/set paths.
- redis_ttl_sec should control TTL.
- Do not break existing constructor usage.

Required:
PoolStateCache(redis_state=FakeRedisState(), redis_ttl_sec=7)
must work.

============================================================
VALIDATION COMMANDS
============================================================

After patch, run:

python -m pytest apex_omega_core/tests/test_dashboard_readiness.py
python -m pytest apex_omega_core/tests/test_pool_state_cache.py
python -m pytest apex_omega_core/tests/test_slippage_sentinel.py
python -m pytest apex_omega_core/tests/test_dual_punch.py
python -m pytest apex_omega_core/tests/test_glass_wall.py
python -m pytest

Return:
1. Files changed
2. Exact fix applied for readiness
3. Exact fix applied for PoolFamily inference
4. Exact fix applied for PoolStateCache constructor
5. Tests run and pass/fail counts
6. Any remaining blockers

