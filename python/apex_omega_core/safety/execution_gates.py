from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Optional


EXECUTABLE_MIN_TVL_USD = 1_000.0
MAX_POOL_USAGE = 0.15
MAX_REASONABLE_SPREAD_BPS = 5_000.0
PAYLOAD_SIM_TOLERANCE_BPS = 25.0
MAX_RESERVE_STALENESS_SECONDS = 30

# Flashloan provider filtering (additional edge)
ALLOWED_FLASHLOAN_PROVIDERS = {"aave", "aave_v3", "balancer", "curve"}
MIN_EXPECTED_PROFIT_USD = 25.0  # Minimum profit threshold for guaranteed routes


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
    FLASHLOAN_PROVIDER_BLOCKED = "FLASHLOAN_PROVIDER_BLOCKED"
    INSUFFICIENT_EXPECTED_PROFIT = "INSUFFICIENT_EXPECTED_PROFIT"


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
    return bool(_get(c, "rpc_healthy", False))


def pool_type_supported(c: Any) -> bool:
    pool_type = str(_get(c, "pool_type", _get(c, "type", ""))).upper()
    return pool_type in SUPPORTED_POOL_TYPES


def reserves_valid(c: Any) -> bool:
    r0 = _get(c, "reserve0", _get(c, "reserve_in", None))
    r1 = _get(c, "reserve1", _get(c, "reserve_out", None))
    verified = bool(_get(c, "reserves_verified", False))
    stale_seconds = float(_get(c, "reserve_staleness_seconds", float("inf")))

    if r0 is None or r1 is None:
        return False
    if not math.isfinite(float(r0)) or not math.isfinite(float(r1)):
        return False
    if float(r0) <= 0 or float(r1) <= 0:
        return False
    if not verified:
        return False
    if not math.isfinite(stale_seconds) or stale_seconds > MAX_RESERVE_STALENESS_SECONDS:
        return False

    return True


def is_dust_pool(c: Any) -> bool:
    tvl = float(_get(c, "tvl_usd", 0))
    return not math.isfinite(tvl) or tvl <= 0 or tvl < EXECUTABLE_MIN_TVL_USD


def flash_size_safe(c: Any) -> bool:
    amount = float(_get(c, "amount_in_usd", _get(c, "flash_amount_usd", 0)))
    weakest_pool_tvl = float(_get(c, "weakest_pool_tvl_usd", _get(c, "tvl_usd", 0)))

    if not math.isfinite(amount) or not math.isfinite(weakest_pool_tvl):
        return False
    if amount <= 0 or weakest_pool_tvl <= 0:
        return False

    return amount <= weakest_pool_tvl * MAX_POOL_USAGE


def is_absurd_spread(c: Any) -> bool:
    spread_bps = abs(float(_get(c, "raw_spread_bps", _get(c, "spread_bps", 0))))
    profit_usd = float(_get(c, "expected_profit_usd", _get(c, "net_profit_usd", 0)))
    amount = float(_get(c, "amount_in_usd", _get(c, "flash_amount_usd", 1)))

    if not math.isfinite(spread_bps) or spread_bps > MAX_REASONABLE_SPREAD_BPS:
        return True
    if not math.isfinite(profit_usd) or not math.isfinite(amount):
        return True
    if amount > 0:
        profit_ratio = profit_usd / amount
        if profit_ratio > 2.0:
            return True

    return False


def is_v3_candidate(c: Any) -> bool:
    pool_type = str(_get(c, "pool_type", _get(c, "type", ""))).upper()
    return pool_type in {"V3", "UNISWAP_V3", "ALGEBRA"}


def is_flashloan_provider_allowed(c: Any) -> bool:
    """Check if flashloan provider is in allowed list (additional edge)."""
    provider = str(_get(c, "flashloan_provider", "")).lower()
    return provider in ALLOWED_FLASHLOAN_PROVIDERS


def is_guaranteed_route(c: Any) -> bool:
    """Check if route meets guaranteed profit threshold (additional edge)."""
    expected_profit = float(_get(c, "expected_profit", _get(c, "expected_profit_usd", 0)))
    return expected_profit >= MIN_EXPECTED_PROFIT_USD


def filter_routes_by_flashloan_provider(routes: list, allowed_providers: Optional[set] = None) -> list:
    """
    Filter routes by allowed flashloan providers (additional edge).
    
    Args:
        routes: List of route dictionaries
        allowed_providers: Set of allowed provider names (defaults to ALLOWED_FLASHLOAN_PROVIDERS)
    
    Returns:
        Filtered list of routes
    """
    if allowed_providers is None:
        allowed_providers = ALLOWED_FLASHLOAN_PROVIDERS
    
    filtered = []
    for route in routes:
        provider = str(_get(route, "flashloan_provider", "")).lower()
        if provider in allowed_providers:
            filtered.append(route)
    return filtered


def get_guaranteed_routes(routes: list) -> list:
    """
    Filter routes to only guaranteed profitable ones (additional edge).
    
    Args:
        routes: List of route dictionaries
    
    Returns:
        Filtered list of guaranteed routes
    """
    guaranteed = []
    for route in routes:
        if is_guaranteed_route(route):
            guaranteed.append(route)
    return guaranteed


def reject_candidate(c: Any) -> Optional[str]:
    if not rpc_healthy(c):
        return RejectReason.RPC_UNHEALTHY.value

    tvl_usd = float(_get(c, "tvl_usd", 0))
    if not math.isfinite(tvl_usd) or tvl_usd < EXECUTABLE_MIN_TVL_USD:
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

    # Additional edge: Flashloan provider filtering
    if not is_flashloan_provider_allowed(c):
        return RejectReason.FLASHLOAN_PROVIDER_BLOCKED.value

    # Additional edge: Guaranteed route profit threshold
    if not is_guaranteed_route(c):
        return RejectReason.INSUFFICIENT_EXPECTED_PROFIT.value

    return None


def gate_candidate(c: Any) -> GateResult:
    reason = reject_candidate(c)
    if reason:
        return GateResult(False, reason, {"candidate": _get(c, "candidate_id", None)})
    return GateResult(True, None, {"candidate": _get(c, "candidate_id", None)})
