APEX-OMEGA CURRENT TEST FAILURE PATCH

Fix only these regressions:

1. DASHBOARD READINESS
Failing:
- test_dashboard_health_uses_readiness_report
- test_dashboard_status_exposes_readiness_report

Patch /healthz and /api/status so:
- production_ready is True when modules_loaded == modules_total
- do NOT enable live execution
- keep execution_enabled / broadcast_enabled / live_execution_ready false unless explicitly enabled by env

2. SLIPPAGE_SENTINEL UNKNOWN_POOL_FAMILY REGRESSION
Failing:
- test_dual_punch.py
- test_glass_wall.py
- test_slippage_sentinel.py

Problem:
Legacy synthetic V2 CPMM test routes have reserve_in, reserve_out, fee, venue, pair, but no pool_family.
Current code classifies them UNKNOWN and rejects.

Patch python/apex_omega_core/core/slippage_sentinel.py:
- Add resolver that infers PoolFamily.V2_CPMM only when:
  reserve_in > 0
  reserve_out > 0
  fee exists
  no explicit pool_family/family/pool_type exists
- Respect explicit pool_family when present.
- Explicit UNKNOWN must still reject.
- Explicit V3/Algebra/CLMM must not fall back to V2 math.
- V3/Algebra must remain separate from V2 CPMM math.
- Do not simply silence UNKNOWN rejection.

3. PoolStateCache CONSTRUCTOR COMPATIBILITY
Failing:
- test_pool_state_cache_writes_through_to_redis
- test_pool_state_cache_hydrates_from_redis_on_miss

Problem:
PoolStateCache(redis_state=..., redis_ttl_sec=7) raises unexpected keyword arg.

Patch PoolStateCache.__init__ to accept:
- redis_state=None
- redis_ttl_sec=None

Map these aliases to the existing redis/cache adapter and ttl fields without breaking current constructor usage.

CANON PRESERVATION:
- Only C1 and C2 are decision authorities.
- Execution remains mechanical.
- C2 does not approve C1.
- C2 evaluates only post-C1 state.
- Punch 2 is a fresh recompute from mutated state.
- Live execution remains disabled.

After patch, run:
python -m pytest apex_omega_core/tests/test_dashboard_readiness.py
python -m pytest apex_omega_core/tests/test_pool_state_cache.py
python -m pytest apex_omega_core/tests/test_slippage_sentinel.py
python -m pytest apex_omega_core/tests/test_dual_punch.py
python -m pytest apex_omega_core/tests/test_glass_wall.py
python -m pytest

Return files changed, fixes applied, and test results.
