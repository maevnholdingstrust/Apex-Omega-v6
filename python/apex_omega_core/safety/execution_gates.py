from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from apex_omega_core.v3.v3_route_validator import is_v3_candidate_validated


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

    if is_v3_candidate(c) and not is_v3_candidate_validated(c, require_fork=False):
        return RejectReason.V3_NOT_VALIDATED.value

    if not flash_size_safe(c):
        return RejectReason.UNSAFE_FLASH_SIZE.value

    if is_absurd_spread(c):
        return RejectReason.ABSURD_SPREAD.value

    if not _get(c, "route_calldata", None):
        return RejectReason.MISSING_CALLDATA.value

    return None


def reject_executable_candidate(c: Any, fork_result: Any = None) -> Optional[str]:
    reason = reject_candidate(c)
    if reason:
        return reason

    if fork_result is None:
        return RejectReason.FORK_SIM_FAILED.value

    accepted = bool(_get(fork_result, "accepted", _get(fork_result, "success", False)))
    if not accepted:
        return RejectReason.FORK_SIM_FAILED.value

    expected_out = float(_get(fork_result, "expected_out", _get(c, "expected_out", 0)))
    simulated_out = float(_get(fork_result, "simulated_out", _get(fork_result, "final_out", 0)))
    tolerance_bps = float(_get(c, "payload_sim_tolerance_bps", PAYLOAD_SIM_TOLERANCE_BPS))
    allowed_delta = abs(expected_out) * (tolerance_bps / 10_000.0)
    if abs(simulated_out - expected_out) > allowed_delta:
        return RejectReason.PAYLOAD_OUTPUT_MISMATCH.value

    return None


def gate_candidate(c: Any) -> GateResult:
    reason = reject_candidate(c)
    if reason:
        return GateResult(False, reason, {"candidate": _get(c, "candidate_id", None)})
    return GateResult(True, None, {"candidate": _get(c, "candidate_id", None)})


def gate_executable_candidate(c: Any, fork_result: Any) -> GateResult:
    reason = reject_executable_candidate(c, fork_result)
    if reason:
        return GateResult(False, reason, {"candidate": _get(c, "candidate_id", None)})
    return GateResult(True, None, {"candidate": _get(c, "candidate_id", None)})
