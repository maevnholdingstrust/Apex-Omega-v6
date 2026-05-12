"""Liquidation / ultimate arbitrage executor (C2) contract wrapper.

Wraps on-chain calls to the ``UltimateArbitrageExecutor`` Solidity
contract (``contracts/UltimateArbitrageExecutor.sol``).  All contract
metadata – address, ABI, function signatures – is sourced from
:mod:`backend.executor_registry`; nothing is hard-coded here.

Typical usage
-------------
::

    from backend.liquidation_executor_contract import LiquidationExecutorContract

    c2 = LiquidationExecutorContract(chain_id=137)
    result = c2.execute_arbitrage(
        asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        amount=50_000_000_000,
        min_profit=100_000_000,
        merkle_proof=[],
        payload=b"<encoded_ultimate_envelope>",
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from web3 import Web3

from backend.contract_interface import ContractInterface
from backend.executor_registry import STRATEGY_C2, ValidationResult

logger = logging.getLogger(__name__)


class LiquidationExecutorContract:
    """High-level interface to the C2 UltimateArbitrageExecutor contract.

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
            chain_id, STRATEGY_C2, rpc_url=rpc_url
        )

    # ── convenience properties ────────────────────────────────────────────

    @property
    def address(self) -> str:
        return self._iface.address

    @property
    def chain_id(self) -> int:
        return self._iface.entry.chain_id

    # ── entry points ─────────────────────────────────────────────────────

    def execute_arbitrage(
        self,
        asset: str,
        amount: int,
        min_profit: int,
        merkle_proof: Sequence[bytes],
        payload: bytes,
    ) -> Dict[str, Any]:
        """Build calldata for ``executeArbitrage`` and simulate or send.

        Parameters
        ----------
        asset:
            Flash-loan token address.
        amount:
            Flash-loan size in token base units.
        min_profit:
            Minimum acceptable profit in token base units.
        merkle_proof:
            Sequence of 32-byte Merkle proof elements.  Pass an empty
            sequence when no route-level Merkle verification is required.
        payload:
            ABI-encoded ultimate route envelope.

        Returns
        -------
        dict
            Result dict with keys ``calldata``, ``simulation``,
            ``broadcast``, and ``dry_run``.
        """
        proof_list: List[bytes] = [
            (bytes(p) if not isinstance(p, bytes) else p)
            for p in merkle_proof
        ]
        sig = "executeArbitrage(address,uint256,uint256,bytes32[],bytes)"
        calldata = self._iface.encode_call(
            sig,
            ["address", "uint256", "uint256", "bytes32[]", "bytes"],
            [
                Web3.to_checksum_address(asset),
                amount,
                min_profit,
                proof_list,
                payload,
            ],
        )
        return self._dispatch(calldata, sig)

    # ── admin functions ───────────────────────────────────────────────────

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
        simulation = self._iface.eth_call(calldata)
        result: Dict[str, Any] = {
            "target": self.address,
            "calldata": calldata,
            "label": label,
            "simulation": simulation,
            "broadcast": None,
            "dry_run": self.dry_run,
        }
        if self.dry_run:
            result["broadcast"] = {"status": "not_sent", "reason": "dry_run=True"}
        return result

    def __repr__(self) -> str:
        return (
            f"LiquidationExecutorContract("
            f"chain={self.chain_id}, "
            f"address={self._iface.entry.address!r}, "
            f"dry_run={self.dry_run})"
        )
