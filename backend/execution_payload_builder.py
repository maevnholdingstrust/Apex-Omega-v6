"""Execution payload builder for Apex-Omega v6.

Assembles the full ABI-encoded execution payload from a strategy-level
opportunity description, enforcing:

* Correct recipient semantics – every swap step names the executor contract
  as its recipient so output tokens land where flash-loan repayment expects them.
* Pool-derived fee tiers – :func:`~backend.protocol_adapters.resolve_pool_fee_info`
  derives fee / router from explicit pool metadata; no hardcoded ``3000``.
* Fail-closed protocol dispatch – :func:`~backend.protocol_adapters.get_adapter`
  raises :exc:`~backend.protocol_adapters.UnknownDexError` for any unrecognised
  DEX key; there is no silent fallback.
* Required pre-broadcast ``eth_call`` simulation – :meth:`ExecutionPayloadBuilder.simulate`
  is a mandatory gate; :meth:`ExecutionPayloadBuilder.build_and_simulate` runs it
  automatically and raises :exc:`~backend.protocol_adapters.SimulationFailedError`
  on revert.

Typical usage
-------------
::

    from backend.execution_payload_builder import ExecutionPayloadBuilder

    builder = ExecutionPayloadBuilder(
        executor_address="0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD",
        w3=web3_instance,  # required for simulation
    )
    result = builder.build_and_simulate(opportunity)
    # result["payload"] is ready to pass to InstitutionalExecutor.init_aave_flash
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from web3 import Web3

from backend.protocol_adapters import (
    PROTOCOL_UNISWAP_V2,
    PROTOCOL_UNISWAP_V3,
    PROTOCOL_ALGEBRA,
    PROTOCOL_CURVE,
    PROTOCOL_BALANCER,
    PoolFeeInfo,
    SimulationFailedError,
    UnknownDexError,
    encode_swap_step,
    resolve_pool_fee_info,
)

logger = logging.getLogger(__name__)

# Canonical executor step tuple types – must match InstitutionalExecutor.sol.
_INSTITUTIONAL_STEP_TYPE = (
    "(uint8,address,address,address,uint256,uint256,uint256,uint16,bytes)"
)

# ---------------------------------------------------------------------------
# SwapStep dataclass
# ---------------------------------------------------------------------------


@dataclass
class SwapStep:
    """A fully-resolved, encoded swap step for the executor payload.

    Parameters
    ----------
    dex_key:
        Canonical DEX identifier (e.g. ``"uniswap-v3"``).
    protocol_id:
        Integer protocol code for the executor contract.
    router:
        Checksummed router / vault address.
    token_in:
        Input token address.
    token_out:
        Output token address.
    amount_in:
        Exact input amount in token base units.
    min_amount_out:
        Minimum accepted output amount.
    fee_bps:
        Pool fee in basis points (for informational / event logging use).
    calldata:
        Fully ABI-encoded router calldata (4-byte selector + encoded args).
    """

    dex_key: str
    protocol_id: int
    router: str
    token_in: str
    token_out: str
    amount_in: int
    min_amount_out: int
    fee_bps: int
    calldata: bytes

    def as_institutional_step(self) -> Dict[str, Any]:
        """Convert to the dict format expected by
        :class:`~python.apex_omega_core.core.execution_compiler.EnvelopeCompiler`.
        """
        return {
            "protocol": self.protocol_id,
            "target": Web3.to_checksum_address(self.router),
            "approveToken": Web3.to_checksum_address(self.token_in),
            "outputToken": Web3.to_checksum_address(self.token_out),
            "callValue": 0,
            "minAmountIn": self.amount_in,
            "minAmountOut": self.min_amount_out,
            "feeBps": self.fee_bps,
            "data": self.calldata,
        }

    def as_ultimate_step(self) -> Dict[str, Any]:
        """Convert to the dict format expected by the C2 ultimate envelope."""
        return {
            "protocol": self.protocol_id,
            "target": Web3.to_checksum_address(self.router),
            "approveToken": Web3.to_checksum_address(self.token_in),
            "callValue": 0,
            "minAmountIn": self.amount_in,
            "minAmountOut": self.min_amount_out,
            "feeBps": self.fee_bps,
            "data": self.calldata,
        }


# ---------------------------------------------------------------------------
# BuildResult
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    """Output of :meth:`ExecutionPayloadBuilder.build_and_simulate`.

    Attributes
    ----------
    steps:
        Ordered list of :class:`SwapStep` objects.
    payload:
        ABI-encoded envelope bytes, ready for the flash-loan entry point.
    asset:
        Flash-loan token address (checksummed).
    min_profit:
        Minimum acceptable profit in token base units.
    simulation:
        Raw result dict from the ``eth_call`` simulation
        (``{"ok": bool, "output": str|None, "error": str|None}``).
    skipped:
        ``True`` when the simulation gate blocked the payload from being
        considered broadcast-ready.
    """

    steps: List[SwapStep] = field(default_factory=list)
    payload: bytes = b""
    asset: str = ""
    min_profit: int = 0
    simulation: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.as_institutional_step() for s in self.steps],
            "payload": self.payload.hex(),
            "asset": self.asset,
            "min_profit": self.min_profit,
            "simulation": self.simulation,
            "skipped": self.skipped,
        }


# ---------------------------------------------------------------------------
# ExecutionPayloadBuilder
# ---------------------------------------------------------------------------


class ExecutionPayloadBuilder:
    """Build and simulate execution payloads for Apex-Omega executor contracts.

    Parameters
    ----------
    executor_address:
        Checksummed address of the deployed executor contract.  This is used
        as the *recipient* of every swap so output tokens arrive at the
        contract before repayment logic runs.
    w3:
        Connected :class:`web3.Web3` instance.  Required when calling
        :meth:`simulate` or :meth:`build_and_simulate`.  Can be ``None``
        for offline payload construction only.
    """

    def __init__(
        self,
        executor_address: str,
        *,
        w3: Optional[Web3] = None,
    ):
        self.executor_address = Web3.to_checksum_address(executor_address)
        self._w3 = w3

    # ── step building ─────────────────────────────────────────────────────

    def build_step(
        self,
        dex_key: str,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        fee_tier: int,
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> SwapStep:
        """Build a single :class:`SwapStep` for the given venue.

        Parameters
        ----------
        dex_key:
            Canonical DEX key (see :mod:`backend.protocol_adapters`).
        token_in / token_out:
            Token addresses.
        amount_in:
            Exact input in token base units.
        min_amount_out:
            Minimum output.
        fee_tier:
            Pool fee in micro-units (e.g. 3000 = 0.3 %).  Must be the
            actual pool fee — not a hardcoded constant.
        extra:
            Optional venue-specific parameters (e.g. ``"pool"`` for Curve,
            ``"pool_id"`` for Balancer).

        Raises
        ------
        UnknownDexError
            For an unsupported *dex_key*.
        """
        pool_info: PoolFeeInfo = resolve_pool_fee_info(dex_key, fee_tier)

        step_params: Dict[str, Any] = {
            "token_in": token_in,
            "token_out": token_out,
            "amount_in": amount_in,
            "min_amount_out": min_amount_out,
            "recipient": self.executor_address,
        }
        if extra:
            step_params.update(extra)

        calldata = encode_swap_step(dex_key, pool_info, step_params)

        # fee_bps: V3 fee_tier is in micro-units → bps = fee_tier / 100
        # V2/Curve/Balancer: use 30 bps as conventional display value
        fee_bps = (fee_tier // 100) if fee_tier > 0 else 30

        return SwapStep(
            dex_key=dex_key,
            protocol_id=_PROTOCOL_FOR_DEX[dex_key],
            router=pool_info.router_address,
            token_in=Web3.to_checksum_address(token_in),
            token_out=Web3.to_checksum_address(token_out),
            amount_in=int(amount_in),
            min_amount_out=int(min_amount_out),
            fee_bps=int(fee_bps),
            calldata=calldata,
        )

    # ── envelope building ─────────────────────────────────────────────────

    def build_institutional_envelope(
        self,
        steps: Sequence[SwapStep],
        *,
        asset: str,
        gas_reserve: int = 0,
        dex_fee_reserve: int = 0,
        version: int = 1,
    ) -> bytes:
        """Assemble the ABI-encoded institutional route envelope.

        Parameters
        ----------
        steps:
            Ordered :class:`SwapStep` list.
        asset:
            Flash-loan / profit token address.
        gas_reserve / dex_fee_reserve:
            Optional reserve amounts forwarded in the envelope header.
        version:
            Envelope version byte (default 1).
        """
        from eth_abi import encode as abi_encode

        if not steps:
            raise ValueError("Institutional envelope requires at least one step")

        encoded_steps = [self._encode_institutional_step(s) for s in steps]
        return abi_encode(
            ["uint8", "address", "uint256", "uint256", f"{_INSTITUTIONAL_STEP_TYPE}[]"],
            [
                int(version),
                Web3.to_checksum_address(asset),
                int(gas_reserve),
                int(dex_fee_reserve),
                encoded_steps,
            ],
        )

    @staticmethod
    def _encode_institutional_step(step: SwapStep) -> tuple:
        return (
            step.protocol_id,
            Web3.to_checksum_address(step.router),
            Web3.to_checksum_address(step.token_in),
            Web3.to_checksum_address(step.token_out),
            0,                    # callValue
            step.amount_in,
            step.min_amount_out,
            step.fee_bps,
            step.calldata,
        )

    # ── simulation ────────────────────────────────────────────────────────

    def simulate(
        self,
        calldata_hex: str,
        *,
        from_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run ``eth_call`` against the executor contract.

        This is a **required** gate that must pass before any broadcast is
        attempted.  The result dict has ``{"ok": bool, "output": ..., "error": ...}``.

        Parameters
        ----------
        calldata_hex:
            ``"0x"``-prefixed ABI-encoded transaction calldata.
        from_address:
            Optional ``from`` override (useful for simulating as the operator).

        Raises
        ------
        RuntimeError
            When no Web3 instance was supplied at construction time.
        """
        if self._w3 is None:
            raise RuntimeError(
                "ExecutionPayloadBuilder requires a Web3 instance to run "
                "eth_call simulations.  Pass w3= at construction."
            )
        call_params: Dict[str, Any] = {
            "to": self.executor_address,
            "data": calldata_hex,
        }
        if from_address:
            call_params["from"] = Web3.to_checksum_address(from_address)
        try:
            output = self._w3.eth.call(call_params)
            return {"ok": True, "output": Web3.to_hex(output), "error": None}
        except Exception as exc:
            return {"ok": False, "output": None, "error": str(exc)}

    # ── high-level build + simulate ───────────────────────────────────────

    def build_and_simulate(
        self,
        opportunity: Mapping[str, Any],
        *,
        from_address: Optional[str] = None,
        flash_entry_calldata: Optional[str] = None,
    ) -> BuildResult:
        """Build the payload and run the required pre-broadcast simulation.

        Parameters
        ----------
        opportunity:
            Strategy-level dict.  Expected keys:

            * ``"asset"`` – flash-loan token address
            * ``"min_profit"`` – minimum profit in token base units
            * ``"steps"`` – list of step dicts, each with:

              - ``"dex_key"``      – canonical DEX key
              - ``"token_in"``     – input token address
              - ``"token_out"``    – output token address
              - ``"amount_in"``    – input amount
              - ``"min_amount_out"`` – minimum output
              - ``"fee_tier"``     – pool fee in micro-units
              - (venue-specific optional keys: ``"pool"``, ``"pool_id"``, etc.)

        from_address:
            Optional ``from`` for the simulation ``eth_call``.
        flash_entry_calldata:
            Pre-built flash-loan entry calldata to simulate.  When absent the
            builder simulates the envelope payload directly against the
            executor address.

        Returns
        -------
        BuildResult
            ``skipped=True`` when simulation fails.

        Raises
        ------
        UnknownDexError
            When any step has an unrecognised DEX key.
        SimulationFailedError
            When the ``eth_call`` simulation reverts.  Callers that want a
            soft failure should catch this; the broadcast layer must never
            proceed past a simulation failure.
        """
        asset = str(opportunity["asset"])
        min_profit = int(opportunity["min_profit"])
        raw_steps: List[Mapping[str, Any]] = list(opportunity["steps"])

        built_steps: List[SwapStep] = []
        for raw in raw_steps:
            step = self.build_step(
                dex_key=raw["dex_key"],
                token_in=raw["token_in"],
                token_out=raw["token_out"],
                amount_in=int(raw["amount_in"]),
                min_amount_out=int(raw["min_amount_out"]),
                fee_tier=int(raw.get("fee_tier", 0)),
                extra={k: v for k, v in raw.items()
                       if k not in ("dex_key", "token_in", "token_out",
                                    "amount_in", "min_amount_out", "fee_tier")},
            )
            built_steps.append(step)

        payload = self.build_institutional_envelope(
            built_steps,
            asset=asset,
            gas_reserve=int(opportunity.get("gas_reserve", 0)),
            dex_fee_reserve=int(opportunity.get("dex_fee_reserve", 0)),
        )

        # Determine what to simulate: prefer the explicit flash-entry calldata
        # so the simulation matches the exact transaction that will be broadcast.
        simulate_calldata = flash_entry_calldata or ("0x" + payload.hex())
        simulation = self.simulate(simulate_calldata, from_address=from_address)

        if not simulation["ok"]:
            logger.warning(
                "Pre-broadcast eth_call simulation failed for executor=%s: %s",
                self.executor_address,
                simulation["error"],
            )
            raise SimulationFailedError(
                f"eth_call simulation reverted: {simulation['error']}"
            )

        logger.debug(
            "eth_call simulation passed for executor=%s payload_len=%d",
            self.executor_address,
            len(payload),
        )

        return BuildResult(
            steps=built_steps,
            payload=payload,
            asset=Web3.to_checksum_address(asset),
            min_profit=min_profit,
            simulation=simulation,
            skipped=False,
        )


# ---------------------------------------------------------------------------
# Protocol ID lookup (module-private)
# ---------------------------------------------------------------------------

_PROTOCOL_FOR_DEX: Dict[str, int] = {
    "quickswap-v2": PROTOCOL_UNISWAP_V2,
    "sushi-v2":     PROTOCOL_UNISWAP_V2,
    "uniswap-v3":   PROTOCOL_UNISWAP_V3,
    "quickswap-v3": PROTOCOL_ALGEBRA,
    "curve":        PROTOCOL_CURVE,
    "balancer":     PROTOCOL_BALANCER,
}


__all__ = [
    "SwapStep",
    "BuildResult",
    "ExecutionPayloadBuilder",
]
