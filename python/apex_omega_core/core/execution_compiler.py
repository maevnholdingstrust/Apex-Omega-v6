from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set

from eth_abi import encode
from web3 import Web3

INSTITUTIONAL_STEP_TYPE = "(uint8,address,address,address,uint256,uint256,uint256,uint16,bytes)"
ULTIMATE_STEP_TYPE = "(uint8,address,address,uint256,uint256,uint256,uint16,bytes)"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompiledExecution:
    encoded_payload: bytes
    min_profit: int
    asset: str


class EnvelopeCompiler:
    """Compiler that converts strategy route dicts into strict ABI payloads."""

    def encode_institutional_step(self, step: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            int(step["protocol"]),
            Web3.to_checksum_address(step["target"]),
            Web3.to_checksum_address(step["approveToken"]),
            Web3.to_checksum_address(step["outputToken"]),
            int(step.get("callValue", 0)),
            int(step.get("minAmountIn", 0)),
            int(step.get("minAmountOut", 0)),
            int(step.get("feeBps", 0)),
            bytes(step.get("data", b"")),
        )

    def encode_ultimate_step(self, step: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            int(step["protocol"]),
            Web3.to_checksum_address(step["target"]),
            Web3.to_checksum_address(step["approveToken"]),
            int(step.get("callValue", 0)),
            int(step.get("minAmountIn", 0)),
            int(step.get("minAmountOut", 0)),
            int(step.get("feeBps", 0)),
            bytes(step.get("data", b"")),
        )

    def build_institutional_envelope(self, route: Mapping[str, Any]) -> bytes:
        steps = [self.encode_institutional_step(step) for step in route["steps"]]
        if not steps:
            raise ValueError("institutional envelope requires at least one step")

        return encode(
            ["uint8", "address", "uint256", "uint256", f"{INSTITUTIONAL_STEP_TYPE}[]"],
            [
                int(route.get("version", 1)),
                Web3.to_checksum_address(route["profitToken"]),
                int(route.get("gasReserveAsset", 0)),
                int(route.get("dexFeeReserveAsset", 0)),
                steps,
            ],
        )

    def build_ultimate_envelope(self, route: Mapping[str, Any]) -> bytes:
        steps = [self.encode_ultimate_step(step) for step in route["steps"]]
        if not steps:
            raise ValueError("ultimate envelope requires at least one step")

        return encode(
            ["uint8", "address", "uint256", "uint256", f"{ULTIMATE_STEP_TYPE}[]"],
            [
                int(route.get("version", 1)),
                Web3.to_checksum_address(route["profitToken"]),
                int(route.get("gasReserveAsset", 0)),
                int(route.get("dexFeeReserveAsset", 0)),
                steps,
            ],
        )


class FlashloanPayloadBuilder:
    """Build callback payloads for InstitutionalExecutor flashloan callbacks."""

    @staticmethod
    def build_aave_payload(min_profit: int, route_envelope: bytes) -> bytes:
        return encode(["uint256", "bytes"], [int(min_profit), bytes(route_envelope)])

    @staticmethod
    def build_balancer_payload(asset: str, amount: int, min_profit: int, route_envelope: bytes) -> bytes:
        return encode(
            ["address", "uint256", "uint256", "bytes"],
            [Web3.to_checksum_address(asset), int(amount), int(min_profit), bytes(route_envelope)],
        )


class ExecutionCompiler:
    """Compile strategy output into deterministic contract payloads."""

    def __init__(self, envelope_compiler: EnvelopeCompiler | None = None):
        self.envelope_compiler = envelope_compiler or EnvelopeCompiler()

    def compile_for_institutional(self, strategy_output: Mapping[str, Any]) -> CompiledExecution:
        route = {
            "version": 1,
            "profitToken": strategy_output["asset"],
            "gasReserveAsset": int(strategy_output.get("gas_reserve_asset", 0)),
            "dexFeeReserveAsset": int(strategy_output.get("dex_fee_reserve_asset", 0)),
            "steps": list(strategy_output["steps"]),
        }
        encoded_payload = self.envelope_compiler.build_institutional_envelope(route)
        return CompiledExecution(
            encoded_payload=encoded_payload,
            min_profit=int(strategy_output["min_profit"]),
            asset=Web3.to_checksum_address(strategy_output["asset"]),
        )

    def compile_for_ultimate(self, strategy_output: Mapping[str, Any]) -> CompiledExecution:
        route = {
            "version": 1,
            "profitToken": strategy_output["asset"],
            "gasReserveAsset": int(strategy_output.get("gas_reserve_asset", 0)),
            "dexFeeReserveAsset": int(strategy_output.get("dex_fee_reserve_asset", 0)),
            "steps": list(strategy_output["steps"]),
        }
        encoded_payload = self.envelope_compiler.build_ultimate_envelope(route)
        return CompiledExecution(
            encoded_payload=encoded_payload,
            min_profit=int(strategy_output["min_profit"]),
            asset=Web3.to_checksum_address(strategy_output["asset"]),
        )

    @staticmethod
    def merkle_leaf(encoded_payload: bytes) -> bytes:
        return Web3.keccak(encoded_payload)



def compile_strategy_batch(
    compiler: ExecutionCompiler,
    strategy_outputs: Sequence[Mapping[str, Any]],
    target: str,
) -> List[CompiledExecution]:
    if target not in {"institutional", "ultimate"}:
        raise ValueError("target must be either 'institutional' or 'ultimate'")

    compiled: List[CompiledExecution] = []
    for output in strategy_outputs:
        if target == "institutional":
            compiled.append(compiler.compile_for_institutional(output))
        else:
            compiled.append(compiler.compile_for_ultimate(output))
    return compiled


# ---------------------------------------------------------------------------
# RouteEnvelopeBuilder — production-safe payload construction
# ---------------------------------------------------------------------------

#: Minimum acceptable min_amount_out on any step (in token base units).
_MIN_STEP_AMOUNT_OUT: int = 1

#: Risk buffer applied per-step when cascading min_amount_out values.
_DEFAULT_STEP_RISK_BUFFER: float = 0.005  # 0.5 %


@dataclass
class RouteEnvelopeError(Exception):
    """Raised when a hard guard fires during envelope construction."""
    guard: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover
        return f"RouteEnvelopeError [{self.guard}]: {self.detail}"


@dataclass
class BuiltEnvelope:
    """Output of :class:`RouteEnvelopeBuilder`."""
    version: int
    profit_token: str
    gas_reserve_asset: int
    dex_fee_reserve_asset: int
    steps: List[Dict[str, Any]]
    amount_in: int
    min_profit: int


class RouteEnvelopeBuilder:
    """
    Defensive payload construction system that prevents execution failure.

    Converts an EV-Engine ``ExecutableTrade`` into a validated
    :class:`BuiltEnvelope` ready to be encoded by :class:`EnvelopeCompiler`.

    Hard guards (non-negotiable)
    ----------------------------
    1. Token consistency  — each step's approve_token must equal the previous
       step's output_token.
    2. Min-out sanity     — ``min_amount_out > 0`` and ``< expected_out``.
    3. Profitability      — final step balance >= flashloan_amount + min_profit.
    4. Data non-empty     — each step's ``data`` field must be non-empty bytes.
    5. Target validation  — each step's ``target`` must be a known router.
    6. Approval safety    — ``approve_token`` must match the token being spent.
    7. Flash-loan safety  — ``amount_in > 0``, ``min_profit > 0``, ``len(steps) > 0``.

    Per-step min-out cascade
    ------------------------
    Each step's ``min_amount_out`` is set to::

        expected_step_out * (1 - step_risk_buffer)

    rather than only protecting the final output, so that mid-route failures
    are caught immediately.
    """

    def __init__(
        self,
        known_routers: Optional[Set[str]] = None,
        step_risk_buffer: float = _DEFAULT_STEP_RISK_BUFFER,
        profit_token: str = "0x0000000000000000000000000000000000000000",
        gas_reserve_asset: int = 0,
        dex_fee_reserve_asset: int = 0,
        version: int = 1,
    ) -> None:
        # Normalise to lowercase checksummed addresses for comparison.
        self._known_routers: FrozenSet[str] = frozenset(
            Web3.to_checksum_address(r).lower()
            for r in (known_routers or set())
        )
        self.step_risk_buffer = max(0.0, min(0.5, step_risk_buffer))
        self.profit_token = profit_token
        self.gas_reserve_asset = gas_reserve_asset
        self.dex_fee_reserve_asset = dex_fee_reserve_asset
        self.version = version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        amount_in: float,
        min_out: float,
        route_steps: List[Mapping[str, Any]],
        min_profit: float,
        flashloan_amount: float,
    ) -> BuiltEnvelope:
        """Build and validate a production-safe :class:`BuiltEnvelope`.

        Parameters
        ----------
        amount_in       : trade input size in token base units (float)
        min_out         : minimum acceptable final output (from EV engine)
        route_steps     : list of raw step dicts (one per DEX hop)
        min_profit      : minimum profit required for the trade to succeed
        flashloan_amount: flash-loan principal that must be repaid
        """
        # ---- GUARD 7: Flash-loan safety (pre-step) ---------------------
        int_amount_in = int(max(0, round(amount_in)))
        int_min_profit = int(max(0, round(min_profit)))

        if int_amount_in <= 0:
            raise RouteEnvelopeError("FLASHLOAN_SAFETY", "amount_in must be > 0")
        if int_min_profit <= 0:
            raise RouteEnvelopeError("FLASHLOAN_SAFETY", "min_profit must be > 0")
        if not route_steps:
            raise RouteEnvelopeError("FLASHLOAN_SAFETY", "steps must not be empty")

        built_steps: List[Dict[str, Any]] = []
        previous_output_token: Optional[str] = None

        for idx, raw_step in enumerate(route_steps):
            step = dict(raw_step)
            label = f"step[{idx}]"

            approve_token = self._resolve_address(step, "approve_token", label)
            output_token = self._resolve_address(step, "output_token", label)
            target = self._resolve_address(step, "target", label)
            data: bytes = bytes(step.get("data") or b"")
            expected_step_out = float(step.get("expected_out", 0.0))
            protocol = int(step.get("protocol", 0))
            call_value = int(step.get("call_value", 0))
            fee_bps = int(step.get("fee_bps", 0))

            # ---- GUARD 1: Token consistency ----------------------------
            if previous_output_token is not None:
                if approve_token.lower() != previous_output_token.lower():
                    raise RouteEnvelopeError(
                        "TOKEN_CONSISTENCY",
                        f"{label}: approve_token={approve_token} != "
                        f"previous output_token={previous_output_token}",
                    )

            # ---- GUARD 4: Data non-empty -------------------------------
            if not data:
                raise RouteEnvelopeError(
                    "DATA_NON_EMPTY",
                    f"{label}: step data is empty",
                )

            # ---- GUARD 5: Target validation ----------------------------
            if self._known_routers and target.lower() not in self._known_routers:
                raise RouteEnvelopeError(
                    "TARGET_VALIDATION",
                    f"{label}: target={target} is not a known router",
                )

            # ---- GUARD 6: Approval safety ------------------------------
            # approve_token is the token being spent by this step; it must
            # equal what the previous step produced (enforced in Guard 1
            # once past the first hop) or be explicitly correct.
            # For the first hop we simply verify it is a valid address.
            if not Web3.is_address(approve_token):
                raise RouteEnvelopeError(
                    "APPROVAL_SAFETY",
                    f"{label}: approve_token={approve_token!r} is not a valid address",
                )

            # ---- Min-out cascade (per-step) ----------------------------
            # When expected_step_out is available, derive a per-step guard.
            # Otherwise fall back to the global min_out on the final step.
            if expected_step_out > 0:
                cascade_min = int(max(
                    _MIN_STEP_AMOUNT_OUT,
                    round(expected_step_out * (1.0 - self.step_risk_buffer)),
                ))
            elif idx == len(route_steps) - 1:
                cascade_min = int(max(_MIN_STEP_AMOUNT_OUT, round(min_out)))
            else:
                cascade_min = _MIN_STEP_AMOUNT_OUT

            # ---- GUARD 2: Min-out sanity -------------------------------
            if cascade_min <= 0:
                raise RouteEnvelopeError(
                    "MIN_OUT_SANITY",
                    f"{label}: computed min_amount_out={cascade_min} must be > 0",
                )
            if expected_step_out > 0 and cascade_min >= expected_step_out:
                raise RouteEnvelopeError(
                    "MIN_OUT_SANITY",
                    f"{label}: min_amount_out={cascade_min} must be < expected_out={expected_step_out}",
                )

            built_steps.append({
                "protocol": protocol,
                "target": target,
                "approveToken": approve_token,
                "outputToken": output_token,
                "callValue": call_value,
                "minAmountIn": int_amount_in if idx == 0 else 0,
                "minAmountOut": cascade_min,
                "feeBps": fee_bps,
                "data": data,
            })
            previous_output_token = output_token

        # ---- GUARD 3: Profitability after final step -------------------
        final_min_out = built_steps[-1]["minAmountOut"] if built_steps else 0
        int_flashloan = int(max(0, round(flashloan_amount)))
        if final_min_out < int_flashloan + int_min_profit:
            raise RouteEnvelopeError(
                "PROFITABILITY",
                f"final min_amount_out={final_min_out} < "
                f"flashloan_amount={int_flashloan} + min_profit={int_min_profit}",
            )

        envelope = BuiltEnvelope(
            version=self.version,
            profit_token=self.profit_token,
            gas_reserve_asset=self.gas_reserve_asset,
            dex_fee_reserve_asset=self.dex_fee_reserve_asset,
            steps=built_steps,
            amount_in=int_amount_in,
            min_profit=int_min_profit,
        )

        if not self._validate_envelope(envelope):
            raise RouteEnvelopeError(
                "FINAL_VALIDATION",
                "Envelope failed post-build validation check",
            )

        return envelope

    def to_compiler_input(self, envelope: BuiltEnvelope) -> Dict[str, Any]:
        """Convert a :class:`BuiltEnvelope` to the dict expected by :class:`ExecutionCompiler`."""
        return {
            "asset": envelope.profit_token,
            "min_profit": envelope.min_profit,
            "gas_reserve_asset": envelope.gas_reserve_asset,
            "dex_fee_reserve_asset": envelope.dex_fee_reserve_asset,
            "steps": envelope.steps,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_envelope(envelope: BuiltEnvelope) -> bool:
        """Final post-build validation — all steps must have non-empty data and min_amount_out > 0."""
        for step in envelope.steps:
            if not step.get("data"):
                return False
            if step.get("minAmountOut", 0) == 0:
                return False
        return True

    @staticmethod
    def _resolve_address(step: Dict[str, Any], key: str, label: str) -> str:
        """Extract and checksum an address field, raising a clear error on failure."""
        raw = step.get(key, "")
        if not raw:
            raise RouteEnvelopeError(
                "ADDRESS_MISSING",
                f"{label}: required field '{key}' is missing or empty",
            )
        try:
            return Web3.to_checksum_address(str(raw))
        except Exception as exc:
            raise RouteEnvelopeError(
                "ADDRESS_INVALID",
                f"{label}: field '{key}'={raw!r} is not a valid address — {exc}",
            ) from exc
