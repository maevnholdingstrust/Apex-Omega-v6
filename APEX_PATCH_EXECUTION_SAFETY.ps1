# APEX_PATCH_EXECUTION_SAFETY.ps1
# Run from Apex-Omega-v6 repo root

$ErrorActionPreference = "Stop"

Write-Host "Creating Apex-Omega execution safety patch files..."

# Ensure directories
New-Item -ItemType Directory -Force -Path ".\python\apex_omega_core\safety" | Out-Null
New-Item -ItemType Directory -Force -Path ".\python\apex_omega_core\v3" | Out-Null
New-Item -ItemType Directory -Force -Path ".\python\apex_omega_core\execution" | Out-Null
New-Item -ItemType Directory -Force -Path ".\python\apex_omega_core\tests" | Out-Null

# ------------------------------------------------------------
# 1. HARD EXECUTION GATES
# ------------------------------------------------------------

@'
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


EXECUTABLE_MIN_TVL_USD = 50_000.0
MAX_POOL_USAGE = 0.03
MAX_REASONABLE_SPREAD_BPS = 5_000.0
PAYLOAD_SIM_TOLERANCE_BPS = 25.0
MAX_RESERVE_STALENESS_SECONDS = 30


class RejectReason(str, Enum):
    RPC_UNHEALTHY = "RPC_UNHEALTHY"
    LOW_TVL = "LOW_TVL"
    INVALID_RESERVES = "INVALID_RESERVES"
    DUST_POOL = "DUST_POOL"
    UNSUPPORTED_POOL = "UNSUPPORTED_POOL"
    V3_NOT_VALIDATED = "V3_NOT_VALIDATED"
    UNSAFE_FLASH_SIZE = "UNSAFE_FLASH_SIZE"
    ABSURD_SPREAD = "ABSURD_SPREAD"
    MISSING_CALLDATA = "MISSING_CALLDATA"
    FORK_SIM_FAILED = "FORK_SIM_FAILED"
    PAYLOAD_OUTPUT_MISMATCH = "PAYLOAD_OUTPUT_MISMATCH"


SUPPORTED_POOL_TYPES = {"V2", "UNISWAP_V2", "QUICKSWAP_V2", "SUSHISWAP_V2", "V3", "UNISWAP_V3", "ALGEBRA"}


@dataclass
class GateResult:
    accepted: bool
    reason: Optional[str] = None
    details: Optional[dict] = None


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def rpc_healthy(c: Any) -> bool:
    return bool(_get(c, "rpc_healthy", True))


def pool_type_supported(c: Any) -> bool:
    pool_type = str(_get(c, "pool_type", _get(c, "type", ""))).upper()
    return pool_type in SUPPORTED_POOL_TYPES


def reserves_valid(c: Any) -> bool:
    r0 = _get(c, "reserve0", _get(c, "reserve_in", None))
    r1 = _get(c, "reserve1", _get(c, "reserve_out", None))
    verified = bool(_get(c, "reserves_verified", True))
    stale_seconds = float(_get(c, "reserve_staleness_seconds", 0))

    if r0 is None or r1 is None:
        return False
    if float(r0) <= 0 or float(r1) <= 0:
        return False
    if not verified:
        return False
    if stale_seconds > MAX_RESERVE_STALENESS_SECONDS:
        return False

    return True


def is_dust_pool(c: Any) -> bool:
    tvl = float(_get(c, "tvl_usd", 0))
    return tvl <= 0 or tvl < EXECUTABLE_MIN_TVL_USD


def flash_size_safe(c: Any) -> bool:
    amount = float(_get(c, "amount_in_usd", _get(c, "flash_amount_usd", 0)))
    weakest_pool_tvl = float(_get(c, "weakest_pool_tvl_usd", _get(c, "tvl_usd", 0)))

    if amount <= 0 or weakest_pool_tvl <= 0:
        return False

    return amount <= weakest_pool_tvl * MAX_POOL_USAGE


def is_absurd_spread(c: Any) -> bool:
    spread_bps = abs(float(_get(c, "raw_spread_bps", _get(c, "spread_bps", 0))))
    profit_usd = float(_get(c, "expected_profit_usd", _get(c, "net_profit_usd", 0)))
    amount = float(_get(c, "amount_in_usd", _get(c, "flash_amount_usd", 1)))

    if spread_bps > MAX_REASONABLE_SPREAD_BPS:
        return True

    if amount > 0:
        profit_ratio = profit_usd / amount
        if profit_ratio > 2.0:
            return True

    return False


def is_v3_candidate(c: Any) -> bool:
    pool_type = str(_get(c, "pool_type", _get(c, "type", ""))).upper()
    return pool_type in {"V3", "UNISWAP_V3", "ALGEBRA"}


def reject_candidate(c: Any) -> Optional[str]:
    if not rpc_healthy(c):
        return RejectReason.RPC_UNHEALTHY.value

    if float(_get(c, "tvl_usd", 0)) < EXECUTABLE_MIN_TVL_USD:
        return RejectReason.LOW_TVL.value

    if not reserves_valid(c):
        return RejectReason.INVALID_RESERVES.value

    if is_dust_pool(c):
        return RejectReason.DUST_POOL.value

    if not pool_type_supported(c):
        return RejectReason.UNSUPPORTED_POOL.value

    if is_v3_candidate(c) and not bool(_get(c, "v3_tick_validated", False)):
        return RejectReason.V3_NOT_VALIDATED.value

    if not flash_size_safe(c):
        return RejectReason.UNSAFE_FLASH_SIZE.value

    if is_absurd_spread(c):
        return RejectReason.ABSURD_SPREAD.value

    if not _get(c, "route_calldata", None):
        return RejectReason.MISSING_CALLDATA.value

    return None


def gate_candidate(c: Any) -> GateResult:
    reason = reject_candidate(c)
    if reason:
        return GateResult(False, reason, {"candidate": _get(c, "candidate_id", None)})
    return GateResult(True, None, {"candidate": _get(c, "candidate_id", None)})
'@ | Set-Content ".\python\apex_omega_core\safety\execution_gates.py" -Encoding UTF8

# ------------------------------------------------------------
# 2. FORK VALIDATION HARNESS
# ------------------------------------------------------------

@'
from dataclasses import dataclass
from typing import Any, Optional, Tuple


PAYLOAD_SIM_TOLERANCE_BPS = 25.0


@dataclass
class ForkSimulationResult:
    success: bool
    final_out: float = 0.0
    profit: float = 0.0
    gas_used: int = 0
    revert_reason: Optional[str] = None
    raw: Optional[Any] = None


@dataclass
class ForkValidationResult:
    accepted: bool
    reason: Optional[str]
    expected_out: float
    simulated_out: float
    expected_profit: float
    simulated_profit: float
    details: dict


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def tolerance(value: float, bps: float = PAYLOAD_SIM_TOLERANCE_BPS) -> float:
    return abs(float(value)) * (bps / 10_000.0)


def build_route_envelope(trade: Any) -> Any:
    builder = _get(trade, "build_route_envelope", None)
    if callable(builder):
        return builder()

    payload = _get(trade, "route_envelope", None)
    if payload is not None:
        return payload

    raise ValueError("Missing RouteEnvelope builder or route_envelope on trade")


def encode_payload(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload

    encoder = getattr(payload, "encode", None)
    if callable(encoder):
        encoded = encoder()
        if isinstance(encoded, str):
            return bytes.fromhex(encoded.replace("0x", ""))
        return encoded

    if isinstance(payload, str):
        return bytes.fromhex(payload.replace("0x", ""))

    raise ValueError("Cannot encode RouteEnvelope payload")


def fork_call_executor(calldata: bytes, trade: Any) -> ForkSimulationResult:
    simulator = _get(trade, "fork_simulator", None)

    if callable(simulator):
        result = simulator(calldata)
        if isinstance(result, ForkSimulationResult):
            return result

        return ForkSimulationResult(
            success=bool(_get(result, "success", False)),
            final_out=float(_get(result, "final_out", 0)),
            profit=float(_get(result, "profit", 0)),
            gas_used=int(_get(result, "gas_used", 0)),
            revert_reason=_get(result, "revert_reason", None),
            raw=result,
        )

    raise RuntimeError("No fork simulator configured for trade")


def validate_on_fork(trade: Any) -> Tuple[bool, ForkValidationResult]:
    expected_out = float(_get(trade, "expected_out", 0))
    expected_profit = float(_get(trade, "expected_profit", _get(trade, "expected_profit_usd", 0)))
    min_profit = float(_get(trade, "min_profit", 0))

    try:
        payload = build_route_envelope(trade)
        calldata = encode_payload(payload)
        sim = fork_call_executor(calldata, trade)
    except Exception as exc:
        result = ForkValidationResult(
            accepted=False,
            reason="FORK_SIM_EXCEPTION",
            expected_out=expected_out,
            simulated_out=0.0,
            expected_profit=expected_profit,
            simulated_profit=0.0,
            details={"error": str(exc)},
        )
        return False, result

    if not sim.success:
        result = ForkValidationResult(
            accepted=False,
            reason="SIM_REVERT",
            expected_out=expected_out,
            simulated_out=sim.final_out,
            expected_profit=expected_profit,
            simulated_profit=sim.profit,
            details={"revert_reason": sim.revert_reason, "gas_used": sim.gas_used},
        )
        return False, result

    if abs(sim.final_out - expected_out) > tolerance(expected_out):
        result = ForkValidationResult(
            accepted=False,
            reason="OUTPUT_MISMATCH",
            expected_out=expected_out,
            simulated_out=sim.final_out,
            expected_profit=expected_profit,
            simulated_profit=sim.profit,
            details={"tolerance": tolerance(expected_out), "gas_used": sim.gas_used},
        )
        return False, result

    if sim.profit < min_profit:
        result = ForkValidationResult(
            accepted=False,
            reason="PROFIT_MISMATCH",
            expected_out=expected_out,
            simulated_out=sim.final_out,
            expected_profit=expected_profit,
            simulated_profit=sim.profit,
            details={"min_profit": min_profit, "gas_used": sim.gas_used},
        )
        return False, result

    result = ForkValidationResult(
        accepted=True,
        reason=None,
        expected_out=expected_out,
        simulated_out=sim.final_out,
        expected_profit=expected_profit,
        simulated_profit=sim.profit,
        details={"gas_used": sim.gas_used},
    )
    return True, result
'@ | Set-Content ".\python\apex_omega_core\execution\fork_validator.py" -Encoding UTF8

# ------------------------------------------------------------
# 3. P_EXEC CALIBRATION
# ------------------------------------------------------------

@'
from dataclasses import dataclass


@dataclass
class ExecutionStats:
    attempts: int = 0
    included: int = 0
    reverts: int = 0
    total_latency_blocks: float = 0.0
    total_prediction_error: float = 0.0

    @property
    def inclusion_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.included / self.attempts

    @property
    def revert_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.reverts / self.attempts

    @property
    def avg_latency_blocks(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.total_latency_blocks / self.attempts

    @property
    def avg_prediction_error(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.total_prediction_error / self.attempts


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def blend(model_p_exec: float, observed_inclusion_rate: float, calibration_weight: float = 0.40) -> float:
    model = clamp01(model_p_exec)
    observed = clamp01(observed_inclusion_rate)
    w = clamp01(calibration_weight)
    return clamp01(((1.0 - w) * model) + (w * observed))


def calibrate_p_exec(model_p: float, stats: ExecutionStats, calibration_weight: float = 0.40) -> float:
    return blend(model_p, stats.inclusion_rate, calibration_weight)


def update_stats_after_attempt(
    stats: ExecutionStats,
    included: bool,
    reverted: bool,
    latency_blocks: float,
    expected_out: float,
    actual_out: float,
) -> ExecutionStats:
    stats.attempts += 1
    stats.included += 1 if included else 0
    stats.reverts += 1 if reverted else 0
    stats.total_latency_blocks += max(0.0, float(latency_blocks))
    stats.total_prediction_error += abs(float(expected_out) - float(actual_out))
    return stats
'@ | Set-Content ".\python\apex_omega_core\execution\p_exec_model.py" -Encoding UTF8

# ------------------------------------------------------------
# 4. V3 SEPARATION
# ------------------------------------------------------------

@'
from dataclasses import dataclass
from decimal import Decimal, getcontext

getcontext().prec = 80

Q96 = Decimal(2) ** 96
Q192 = Decimal(2) ** 192


@dataclass(frozen=True)
class V3PoolState:
    token0: str
    token1: str
    fee_bps: int
    sqrt_price_x96: int
    liquidity: int
    tick: int
    tick_spacing: int
    decimals0: int
    decimals1: int
    pool_address: str
    dex: str = "UNISWAP_V3"


def price_token1_per_token0(state: V3PoolState) -> Decimal:
    sqrt_price = Decimal(state.sqrt_price_x96)
    raw_price = (sqrt_price * sqrt_price) / Q192
    decimal_adjustment = Decimal(10) ** Decimal(state.decimals0 - state.decimals1)
    return raw_price * decimal_adjustment


def validate_v3_state(state: V3PoolState) -> bool:
    if state.sqrt_price_x96 <= 0:
        return False
    if state.liquidity <= 0:
        return False
    if state.tick_spacing <= 0:
        return False
    if state.fee_bps < 0:
        return False
    if not state.token0 or not state.token1:
        return False
    return True
'@ | Set-Content ".\python\apex_omega_core\v3\v3_pool_state.py" -Encoding UTF8

@'
from decimal import Decimal, getcontext

getcontext().prec = 80

MIN_TICK = -887272
MAX_TICK = 887272


def tick_to_price(tick: int) -> Decimal:
    if tick < MIN_TICK or tick > MAX_TICK:
        raise ValueError("tick out of Uniswap V3 bounds")
    return Decimal("1.0001") ** Decimal(tick)


def validate_tick_spacing(tick: int, tick_spacing: int) -> bool:
    if tick_spacing <= 0:
        return False
    return tick % tick_spacing == 0


def is_tick_in_bounds(tick: int) -> bool:
    return MIN_TICK <= tick <= MAX_TICK
'@ | Set-Content ".\python\apex_omega_core\v3\v3_tick_math.py" -Encoding UTF8

@'
from decimal import Decimal
from .v3_pool_state import V3PoolState, validate_v3_state, price_token1_per_token0


def quote_v3_spot_exact_in(state: V3PoolState, amount_in: float, zero_for_one: bool) -> float:
    """
    Conservative spot quote only.
    This is NOT full initialized tick traversal.
    Used for gating/validation, not final execution unless external quoter/fork confirms.
    """
    if not validate_v3_state(state):
        raise ValueError("invalid V3 pool state")

    amount = Decimal(str(amount_in))
    fee_multiplier = Decimal(1) - (Decimal(state.fee_bps) / Decimal(10_000))
    price = price_token1_per_token0(state)

    if zero_for_one:
        return float(amount * fee_multiplier * price)

    return float((amount * fee_multiplier) / price)
'@ | Set-Content ".\python\apex_omega_core\v3\v3_swap_math.py" -Encoding UTF8

@'
from dataclasses import dataclass
from typing import Optional
from .v3_pool_state import V3PoolState, validate_v3_state
from .v3_swap_math import quote_v3_spot_exact_in


@dataclass
class V3Quote:
    success: bool
    amount_out: float
    source: str
    reason: Optional[str] = None


def quote_v3_exact_in(state: V3PoolState, amount_in: float, zero_for_one: bool, external_quoter=None) -> V3Quote:
    if not validate_v3_state(state):
        return V3Quote(False, 0.0, "validation", "INVALID_V3_STATE")

    if external_quoter:
        q = external_quoter(state, amount_in, zero_for_one)
        return V3Quote(True, float(q), "external_quoter", None)

    out = quote_v3_spot_exact_in(state, amount_in, zero_for_one)
    return V3Quote(True, out, "spot_math_requires_fork_confirmation", None)
'@ | Set-Content ".\python\apex_omega_core\v3\v3_quoter.py" -Encoding UTF8

@'
from typing import Any
from .v3_pool_state import V3PoolState, validate_v3_state


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_v3_candidate(candidate: Any) -> bool:
    state = _get(candidate, "v3_state", None)

    if not isinstance(state, V3PoolState):
        return False

    if not validate_v3_state(state):
        return False

    if not _get(candidate, "route_calldata", None):
        return False

    if not bool(_get(candidate, "fork_sim_passed", False)):
        return False

    return True
'@ | Set-Content ".\python\apex_omega_core\v3\v3_route_validator.py" -Encoding UTF8

@'
from typing import Any


def build_uniswap_v3_route(candidate: Any) -> dict:
    """
    Mechanical V3 route object.
    Actual calldata must be produced by the router codec or external ABI builder.
    """
    if not getattr(candidate, "v3_tick_validated", False) and not (
        isinstance(candidate, dict) and candidate.get("v3_tick_validated")
    ):
        raise ValueError("V3 route cannot be built without tick validation")

    return {
        "pool_type": "UNISWAP_V3",
        "route": getattr(candidate, "route", None) if not isinstance(candidate, dict) else candidate.get("route"),
        "calldata": getattr(candidate, "route_calldata", None) if not isinstance(candidate, dict) else candidate.get("route_calldata"),
    }
'@ | Set-Content ".\python\apex_omega_core\v3\uniswap_v3_router.py" -Encoding UTF8

@'
from typing import Any


def build_algebra_route(candidate: Any) -> dict:
    if not getattr(candidate, "v3_tick_validated", False) and not (
        isinstance(candidate, dict) and candidate.get("v3_tick_validated")
    ):
        raise ValueError("Algebra route cannot be built without tick validation")

    return {
        "pool_type": "ALGEBRA",
        "route": getattr(candidate, "route", None) if not isinstance(candidate, dict) else candidate.get("route"),
        "calldata": getattr(candidate, "route_calldata", None) if not isinstance(candidate, dict) else candidate.get("route_calldata"),
    }
'@ | Set-Content ".\python\apex_omega_core\v3\algebra_router.py" -Encoding UTF8

@'
from typing import Any
from apex_omega_core.v3.uniswap_v3_router import build_uniswap_v3_route
from apex_omega_core.v3.algebra_router import build_algebra_route


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_v2_route(candidate: Any) -> dict:
    return {
        "pool_type": "V2",
        "route": _get(candidate, "route", None),
        "calldata": _get(candidate, "route_calldata", None),
    }


def build_route(candidate: Any) -> dict:
    pool_type = str(_get(candidate, "pool_type", _get(candidate, "type", ""))).upper()

    if pool_type in {"V3", "UNISWAP_V3"}:
        return build_uniswap_v3_route(candidate)

    if pool_type == "ALGEBRA":
        return build_algebra_route(candidate)

    return build_v2_route(candidate)
'@ | Set-Content ".\python\apex_omega_core\execution\route_builder.py" -Encoding UTF8

# ------------------------------------------------------------
# 5. PIPELINE GATE WRAPPER
# ------------------------------------------------------------

@'
from typing import Any, Tuple

from apex_omega_core.safety.execution_gates import gate_candidate
from apex_omega_core.execution.fork_validator import validate_on_fork


def pre_execution_pipeline(candidate: Any, c1_fn, c2_fn) -> Tuple[bool, str, Any]:
    """
    DISCOVERY
      ↓
    HARD GATES
      ↓
    C1
      ↓
    C2
      ↓
    FORK VALIDATION
      ↓
    EXECUTION
    """

    gate = gate_candidate(candidate)
    if not gate.accepted:
        return False, gate.reason, gate

    c1_result = c1_fn(candidate)
    c2_result = c2_fn(c1_result)

    decision = getattr(c2_result, "decision", None)
    if isinstance(c2_result, dict):
        decision = c2_result.get("decision")

    if decision not in {"STRIKE", True}:
        return False, "C2_DO_NOTHING", c2_result

    fork_ok, fork_result = validate_on_fork(c2_result)

    if not fork_ok:
        return False, fork_result.reason, fork_result

    return True, "READY_FOR_EXECUTION", fork_result
'@ | Set-Content ".\python\apex_omega_core\execution\pre_execution_pipeline.py" -Encoding UTF8

# ------------------------------------------------------------
# 6. TESTS
# ------------------------------------------------------------

@'
from types import SimpleNamespace

from apex_omega_core.safety.execution_gates import reject_candidate
from apex_omega_core.execution.p_exec_model import ExecutionStats, calibrate_p_exec, update_stats_after_attempt


def base_candidate(**overrides):
    data = dict(
        rpc_healthy=True,
        tvl_usd=100_000,
        reserve0=50_000,
        reserve1=50_000,
        reserves_verified=True,
        reserve_staleness_seconds=1,
        pool_type="V2",
        amount_in_usd=1_000,
        weakest_pool_tvl_usd=100_000,
        raw_spread_bps=100,
        expected_profit_usd=20,
        route_calldata=b"1234",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_dust_pool_rejected():
    c = base_candidate(tvl_usd=10)
    assert reject_candidate(c) == "LOW_TVL"


def test_invalid_reserves_rejected():
    c = base_candidate(reserve0=0)
    assert reject_candidate(c) == "INVALID_RESERVES"


def test_unsafe_flash_size_rejected():
    c = base_candidate(amount_in_usd=10_000, weakest_pool_tvl_usd=100_000)
    assert reject_candidate(c) == "UNSAFE_FLASH_SIZE"


def test_v3_without_tick_validation_rejected():
    c = base_candidate(pool_type="V3", v3_tick_validated=False)
    assert reject_candidate(c) == "V3_NOT_VALIDATED"


def test_missing_calldata_rejected():
    c = base_candidate(route_calldata=None)
    assert reject_candidate(c) == "MISSING_CALLDATA"


def test_absurd_spread_rejected():
    c = base_candidate(raw_spread_bps=100_000)
    assert reject_candidate(c) == "ABSURD_SPREAD"


def test_p_exec_calibration():
    stats = ExecutionStats(attempts=10, included=5)
    p = calibrate_p_exec(0.9, stats, calibration_weight=0.4)
    assert round(p, 2) == 0.74


def test_state_prediction_error_logged():
    stats = ExecutionStats()
    update_stats_after_attempt(stats, True, False, 1, 100, 95)
    assert stats.attempts == 1
    assert stats.inclusion_rate == 1
    assert stats.avg_prediction_error == 5
'@ | Set-Content ".\python\apex_omega_core\tests\test_execution_safety_patch.py" -Encoding UTF8

Write-Host "Patch files created."
Write-Host "Running tests..."

Push-Location ".\python"
try {
    python -m pytest apex_omega_core/tests/test_execution_safety_patch.py -q
}
finally {
    Pop-Location
}

Write-Host "Done."