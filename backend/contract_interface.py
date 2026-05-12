"""Low-level contract interface driven by the executor registry.

:class:`ContractInterface` wraps a Web3 connection to a single executor
contract.  It is constructed from an :class:`~backend.executor_registry.ExecutorEntry`
so every address, ABI, and function signature is sourced from the
registry rather than being hard-coded.

Typical usage
-------------
::

    from backend.executor_registry import get_entry, STRATEGY_C1
    from backend.contract_interface import ContractInterface

    iface = ContractInterface.from_registry(chain_id=137, strategy=STRATEGY_C1)
    result = iface.eth_call("initAaveFlash(address,uint256,uint256,bytes)", calldata_hex)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from web3 import Web3

from backend.executor_registry import (
    ExecutorEntry,
    ValidationResult,
    get_entry,
    get_rpc_url,
    validate_registry_entry,
)

logger = logging.getLogger(__name__)


class ContractInterface:
    """Registry-driven interface to an on-chain executor contract.

    All addresses, ABI fragments, and function signatures are taken from
    :class:`~backend.executor_registry.ExecutorEntry`; nothing is
    hard-coded here.

    Parameters
    ----------
    entry:
        Registry entry for the target contract.
    rpc_url:
        Override RPC URL.  Defaults to :func:`~backend.executor_registry.get_rpc_url`
        for the entry's chain ID.
    """

    def __init__(self, entry: ExecutorEntry, *, rpc_url: Optional[str] = None):
        self.entry = entry
        self._rpc_url = rpc_url or get_rpc_url(entry.chain_id)
        self._w3: Optional[Web3] = None

    # ── class factory ─────────────────────────────────────────────────────

    @classmethod
    def from_registry(
        cls,
        chain_id: int,
        strategy: str,
        *,
        rpc_url: Optional[str] = None,
    ) -> "ContractInterface":
        """Construct a :class:`ContractInterface` from the global registry.

        Parameters
        ----------
        chain_id:
            EIP-155 chain identifier.
        strategy:
            ``"institutional"`` (C1) or ``"ultimate"`` (C2).
        rpc_url:
            Optional RPC URL override.
        """
        entry = get_entry(chain_id, strategy)
        return cls(entry, rpc_url=rpc_url)

    # ── Web3 provider ─────────────────────────────────────────────────────

    @property
    def w3(self) -> Web3:
        """Lazily initialised Web3 instance."""
        if self._w3 is None:
            self._w3 = Web3(
                Web3.HTTPProvider(self._rpc_url, request_kwargs={"timeout": 10})
            )
        return self._w3

    @property
    def address(self) -> str:
        """Checksummed contract address."""
        raw = self.entry.address
        if not raw:
            raise ValueError(
                f"Executor address for {self.entry.strategy!r} on chain "
                f"{self.entry.chain_id} is not configured. "
                f"Set env var {self.entry.address_env_var!r}."
            )
        return Web3.to_checksum_address(raw)

    # ── ABI / selector helpers ─────────────────────────────────────────────

    @property
    def abi(self) -> List[Dict[str, Any]]:
        """Return the inline ABI from the registry entry."""
        return self.entry.abi

    def selector(self, signature: str) -> bytes:
        """Return the 4-byte Keccak-256 selector for *signature*."""
        return bytes(self.w3.keccak(text=signature)[:4])

    def encode_call(
        self,
        signature: str,
        arg_types: List[str],
        args: List[Any],
    ) -> str:
        """ABI-encode a function call.

        Parameters
        ----------
        signature:
            Full Solidity function signature, e.g.
            ``"initAaveFlash(address,uint256,uint256,bytes)"``.
        arg_types:
            List of ABI type strings matching the function parameters.
        args:
            List of Python values to encode.

        Returns
        -------
        str
            ``"0x"``-prefixed hex calldata.
        """
        from eth_abi import encode as abi_encode

        sel = self.selector(signature)
        encoded = abi_encode(arg_types, args)
        return Web3.to_hex(sel + encoded)

    # ── on-chain read helpers ──────────────────────────────────────────────

    def eth_call(self, calldata_hex: str) -> Dict[str, Any]:
        """Simulate a call via ``eth_call`` and return a result dict.

        Parameters
        ----------
        calldata_hex:
            ``"0x"``-prefixed encoded calldata.

        Returns
        -------
        dict
            ``{"ok": bool, "output": str | None, "error": str | None}``
        """
        try:
            output = self.w3.eth.call(
                {"to": self.address, "data": calldata_hex}
            )
            return {"ok": True, "output": Web3.to_hex(output), "error": None}
        except Exception as exc:
            return {"ok": False, "output": None, "error": str(exc)}

    def get_owner(self) -> Optional[str]:
        """Call ``owner()`` and return the checksummed owner address, or ``None`` on failure."""
        sel = self.selector("owner()")
        result = self.eth_call(Web3.to_hex(sel))
        if not result["ok"] or not result["output"]:
            return None
        raw_output = Web3.to_bytes(hexstr=result["output"])
        if len(raw_output) < 20:
            return None
        return Web3.to_checksum_address("0x" + raw_output[-20:].hex())

    def get_bytecode(self) -> bytes:
        """Return the deployed bytecode at the contract address."""
        return bytes(self.w3.eth.get_code(self.address))

    # ── Web3 contract object ──────────────────────────────────────────────

    def contract(self) -> Any:
        """Return a ``web3.eth.contract`` instance bound to this entry's ABI and address."""
        return self.w3.eth.contract(address=self.address, abi=self.abi)

    # ── validation shortcut ───────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        """Run startup validation for this entry.  See :func:`validate_registry_entry`."""
        return validate_registry_entry(self.entry, rpc_url=self._rpc_url)

    # ── repr ──────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ContractInterface("
            f"chain={self.entry.chain_id}, "
            f"strategy={self.entry.strategy!r}, "
            f"address={self.entry.address!r})"
        )
