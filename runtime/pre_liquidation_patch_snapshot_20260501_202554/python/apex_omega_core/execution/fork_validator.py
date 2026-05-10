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
