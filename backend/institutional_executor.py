"""Institutional executor (C1) wrapper for Apex-Omega v6.

Wraps on-chain calls to the ``InstitutionalExecutor`` Solidity contract
(``contracts/InstitutionalExecutor.sol``).  All contract metadata –
address, ABI, function signatures – is sourced from
:mod:`backend.executor_registry`; nothing is hard-coded here.

Typical usage
-------------
::

    from backend.institutional_executor import InstitutionalExecutor

    c1 = InstitutionalExecutor(chain_id=137)
    result = c1.init_aave_flash(
        asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        amount=50_000_000_000,   # 50 000 USDC in base units
        min_profit=100_000_000,  # 100 USDC
        payload=b"<encoded_route_envelope>",
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from web3 import Web3

from backend.contract_interface import ContractInterface
from backend.executor_registry import STRATEGY_C1, ValidationResult

logger = logging.getLogger(__name__)


class InstitutionalExecutor:
    """High-level interface to the C1 InstitutionalExecutor contract.

    Parameters
    ----------
    chain_id:
        EIP-155 chain ID of the target network.  Defaults to ``137``
        (Polygon).
    rpc_url:
        Override RPC URL.  Resolved from the registry when absent.
    dry_run:
        When ``True`` (default) all transaction methods perform only an
        ``eth_call`` simulation and do not broadcast anything on-chain.
        Set to ``False`` only when :envvar:`LIVE_TRADING_ENABLED=true`
        and :envvar:`DRY_RUN=false`.
    """

    def __init__(
        self,
        chain_id: int = 137,
        *,
        rpc_url: Optional[str] = None,
        dry_run: bool = True,
    ):
        self.dry_run = dry_run
        self._iface = ContractInterface.from_registry(
            chain_id, STRATEGY_C1, rpc_url=rpc_url
        )

    # ── convenience properties ────────────────────────────────────────────

    @property
    def address(self) -> str:
        return self._iface.address

    @property
    def chain_id(self) -> int:
        return self._iface.entry.chain_id

    # ── entry points ─────────────────────────────────────────────────────

    def init_aave_flash(
        self,
        asset: str,
        amount: int,
        min_profit: int,
        payload: bytes,
    ) -> Dict[str, Any]:
        """Build calldata for ``initAaveFlash`` and simulate or send.

        Parameters
        ----------
        asset:
            Flash-loan token address.
        amount:
            Flash-loan size in token base units.
        min_profit:
            Minimum acceptable profit in token base units.
        payload:
            ABI-encoded route envelope (output of
            :class:`~python.apex_omega_core.core.execution_compiler.ExecutionCompiler`).

        Returns
        -------
        dict
            Result dict with keys ``calldata``, ``simulation``, and
            ``broadcast`` (``None`` in dry-run mode).
        """
        sig = "initAaveFlash(address,uint256,uint256,bytes)"
        calldata = self._iface.encode_call(
            sig,
            ["address", "uint256", "uint256", "bytes"],
            [Web3.to_checksum_address(asset), amount, min_profit, payload],
        )
        return self._dispatch(calldata, sig)

    def init_balancer_flash(
        self,
        asset: str,
        amount: int,
        min_profit: int,
        payload: bytes,
    ) -> Dict[str, Any]:
        """Build calldata for ``initBalancerFlash`` and simulate or send."""
        sig = "initBalancerFlash(address,uint256,uint256,bytes)"
        calldata = self._iface.encode_call(
            sig,
            ["address", "uint256", "uint256", "bytes"],
            [Web3.to_checksum_address(asset), amount, min_profit, payload],
        )
        return self._dispatch(calldata, sig)

    # ── admin functions ───────────────────────────────────────────────────

    def approve_router(
        self, token: str, router: str, amount: int
    ) -> Dict[str, Any]:
        """Build calldata for ``approveRouter`` and simulate or send."""
        sig = "approveRouter(address,address,uint256)"
        calldata = self._iface.encode_call(
            sig,
            ["address", "address", "uint256"],
            [Web3.to_checksum_address(token), Web3.to_checksum_address(router), amount],
        )
        return self._dispatch(calldata, sig)

    def rescue_token(self, token: str) -> Dict[str, Any]:
        """Build calldata for ``rescueToken`` and simulate or send."""
        sig = "rescueToken(address)"
        calldata = self._iface.encode_call(
            sig,
            ["address"],
            [Web3.to_checksum_address(token)],
        )
        return self._dispatch(calldata, sig)

    # ── view ──────────────────────────────────────────────────────────────

    def get_owner(self) -> Optional[str]:
        """Return the checksummed owner address from the contract."""
        return self._iface.get_owner()

    # ── validation ────────────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        """Run startup validation.  See :func:`~backend.executor_registry.validate_registry_entry`."""
        return self._iface.validate()

    # ── internal dispatch ─────────────────────────────────────────────────

    def _dispatch(self, calldata: str, label: str) -> Dict[str, Any]:
        """Run the required eth_call simulation; broadcast only when safe.

        The ``eth_call`` simulation is **mandatory** and runs unconditionally.
        When the simulation reverts the result is returned with
        ``"simulation_failed": True`` and ``broadcast`` is blocked regardless
        of dry-run settings.  This prevents broadcasting transactions that are
        guaranteed to revert on-chain.

        Real signing and broadcasting require ``LIVE_TRADING_ENABLED=true``,
        ``DRY_RUN=false``, and ``EXECUTOR_PRIVATE_KEY`` to be set.  Those
        checks are delegated to the caller's
        :class:`~python.apex_omega_core.core.runtime_config.RuntimeConfig`.
        """
        simulation = self._iface.eth_call(calldata)
        result: Dict[str, Any] = {
            "target": self.address,
            "calldata": calldata,
            "label": label,
            "simulation": simulation,
            "broadcast": None,
            "dry_run": self.dry_run,
            "simulation_failed": not simulation["ok"],
        }

        if not simulation["ok"]:
            logger.warning(
                "eth_call simulation failed for %s on chain %d: %s",
                label,
                self.chain_id,
                simulation.get("error"),
            )
            result["broadcast"] = {
                "status": "not_sent",
                "reason": f"eth_call simulation failed: {simulation.get('error')}",
            }
            return result

        if self.dry_run:
            result["broadcast"] = {"status": "not_sent", "reason": "dry_run=True"}
        return result

    def __repr__(self) -> str:
        return (
            f"InstitutionalExecutor("
            f"chain={self.chain_id}, "
            f"address={self._iface.entry.address!r}, "
            f"dry_run={self.dry_run})"
        )
