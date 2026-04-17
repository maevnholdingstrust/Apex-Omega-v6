"""
MEV Bundle — Flashbots-compatible bundle building, simulation, and submission.

Components
----------
BundleTransaction  – a single EIP-1559 signed transaction destined for a bundle
MEVBundle          – an ordered collection of transactions targeting a specific block
BundleBuilder      – signs EIP-1559 transactions and assembles :class:`MEVBundle` objects
BundleSimulator    – dry-runs a bundle via ``eth_callBundle`` before on-chain submission
BundleSubmitter    – POSTs the bundle to a Flashbots-compatible MEV relay

Environment variables
---------------------
APEX_PRIVATE_KEY          – EOA key used to sign transactions
APEX_RPC_URL              – JSON-RPC node for signing helpers (default: Polygon public RPC)
APEX_SIMULATION_URL       – RPC endpoint for eth_callBundle simulation (falls back to APEX_RPC_URL)
APEX_MEV_RELAY_URL        – MEV relay URL (default: https://relay.flashbots.net)
APEX_FLASHBOTS_SIGNING_KEY – Optional separate key for X-Flashbots-Signature header
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class BundleTransaction:
    """A single signed transaction included in a :class:`MEVBundle`."""
    signed_raw_tx: str      # 0x-prefixed hex RLP-encoded signed transaction
    calldata: str = ""      # human-readable; logged but not sent to relay
    expected_gas: int = 350_000


@dataclass
class MEVBundle:
    """
    An ordered set of transactions targeting a specific block number.

    Attributes
    ----------
    txs            : signed transactions in execution order
    target_block   : the block number this bundle is valid for
    min_profit_wei : minimum profit threshold forwarded to the relay (optional)
    simulation_id  : populated by :class:`BundleSimulator` after a successful dry-run
    submission_id  : bundle hash returned by the relay after submission
    """
    txs: List[BundleTransaction]
    target_block: int
    min_profit_wei: int = 0
    simulation_id: str = ""
    submission_id: str = ""


# ---------------------------------------------------------------------------
# Bundle Builder
# ---------------------------------------------------------------------------

class BundleBuilder:
    """
    Build EIP-1559 signed transactions and assemble :class:`MEVBundle` objects.

    If ``APEX_PRIVATE_KEY`` is not set the builder can still construct unsigned
    bundle shells for simulation-only pipelines (``build_signed_tx`` returns
    ``None`` in that case).
    """

    def __init__(self, w3: Optional[Web3] = None, private_key: Optional[str] = None):
        rpc_url = os.getenv("APEX_RPC_URL", "https://polygon-rpc.com/")
        self.w3 = w3 or Web3(Web3.HTTPProvider(rpc_url))
        self.private_key = private_key or os.getenv("APEX_PRIVATE_KEY")
        self.account = (
            self.w3.eth.account.from_key(self.private_key)
            if self.private_key
            else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_signed_tx(
        self,
        to: str,
        calldata: str,
        gas: int,
        max_fee_per_gas: int,
        max_priority_fee_per_gas: int,
        value: int = 0,
    ) -> Optional[str]:
        """
        Sign an EIP-1559 transaction and return the raw hex string.

        Returns ``None`` when no private key is available (simulation-only mode).
        """
        if self.account is None:
            logger.warning("BundleBuilder: APEX_PRIVATE_KEY not set; cannot sign transaction")
            return None

        nonce = self.w3.eth.get_transaction_count(self.account.address)
        chain_id = self.w3.eth.chain_id

        tx: Dict[str, Any] = {
            "type": 2,
            "chainId": chain_id,
            "nonce": nonce,
            "to": Web3.to_checksum_address(to),
            "value": value,
            "data": calldata,
            "gas": gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
        }

        signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
        return Web3.to_hex(signed.rawTransaction)

    def assemble(
        self,
        calldata: str,
        target_address: str,
        gas: int,
        max_fee_per_gas: int,
        max_priority_fee_per_gas: int,
        min_profit_wei: int = 0,
        block_offset: int = 1,
    ) -> Optional[MEVBundle]:
        """
        Sign ``calldata`` and wrap it in an :class:`MEVBundle` targeting
        ``current_block + block_offset``.

        Returns ``None`` when the transaction could not be signed.
        """
        raw_tx = self.build_signed_tx(
            to=target_address,
            calldata=calldata,
            gas=gas,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
        )
        if raw_tx is None:
            return None

        target_block = self.w3.eth.block_number + block_offset
        return MEVBundle(
            txs=[BundleTransaction(
                signed_raw_tx=raw_tx,
                calldata=calldata,
                expected_gas=gas,
            )],
            target_block=target_block,
            min_profit_wei=min_profit_wei,
        )


# ---------------------------------------------------------------------------
# Bundle Simulator
# ---------------------------------------------------------------------------

class BundleSimulator:
    """
    Dry-run a :class:`MEVBundle` via ``eth_callBundle``.

    Compatible with Flashbots, Tenderly, and any JSON-RPC node that implements
    the ``eth_callBundle`` method.  Returns a structured result dict regardless
    of success or failure.
    """

    _TIMEOUT_SECONDS: int = 15

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = (
            rpc_url
            or os.getenv("APEX_SIMULATION_URL")
            or os.getenv("APEX_RPC_URL", "https://polygon-rpc.com/")
        )

    async def simulate(self, bundle: MEVBundle) -> Dict[str, Any]:
        """
        Submit the bundle to ``eth_callBundle`` and parse the response.

        Returns
        -------
        dict with keys:
          success          : bool
          simulated        : bool  — whether a network call was made
          total_gas_used   : int   — sum of gasUsed across all bundle transactions
          coinbase_diff    : str   — coinbase balance delta (hex)
          results          : list  — per-transaction simulation results
          error            : str   — error message on failure
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_callBundle",
            "params": [
                {
                    "txs": [tx.signed_raw_tx for tx in bundle.txs],
                    "blockNumber": hex(bundle.target_block),
                    "stateBlockNumber": "latest",
                }
            ],
        }

        timeout = aiohttp.ClientTimeout(total=self._TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.rpc_url, json=payload, ssl=False
                ) as response:
                    data = await response.json(content_type=None)
        except Exception as exc:
            logger.warning("Bundle simulation request failed: %s", exc)
            return {
                "success": False,
                "simulated": False,
                "total_gas_used": 0,
                "coinbase_diff": "0x0",
                "results": [],
                "error": str(exc),
            }

        error = data.get("error")
        if error:
            return {
                "success": False,
                "simulated": True,
                "total_gas_used": 0,
                "coinbase_diff": "0x0",
                "results": [],
                "error": str(error),
            }

        result = data.get("result") or {}
        per_tx = result.get("results") or []
        total_gas = sum(
            int(r.get("gasUsed", "0x0"), 16) if isinstance(r.get("gasUsed"), str)
            else int(r.get("gasUsed", 0))
            for r in per_tx
        )

        bundle.simulation_id = result.get("bundleHash", "")

        return {
            "success": True,
            "simulated": True,
            "total_gas_used": total_gas,
            "coinbase_diff": result.get("coinbaseDiff", "0x0"),
            "results": per_tx,
            "error": None,
        }


# ---------------------------------------------------------------------------
# Bundle Submitter
# ---------------------------------------------------------------------------

class BundleSubmitter:
    """
    Submit :class:`MEVBundle` objects to a Flashbots-compatible MEV relay.

    The relay URL and signing key are read from environment variables by
    default and can be overridden at construction time.

    Signing key (``APEX_FLASHBOTS_SIGNING_KEY``) is optional; if absent the
    ``X-Flashbots-Signature`` header is omitted, which is acceptable for
    relays that do not require it (e.g. Polygon private RPCs).
    """

    _TIMEOUT_SECONDS: int = 10

    def __init__(
        self,
        relay_url: Optional[str] = None,
        signing_key: Optional[str] = None,
    ):
        self.relay_url = relay_url or os.getenv(
            "APEX_MEV_RELAY_URL", "https://relay.flashbots.net"
        )
        self.signing_key = signing_key or os.getenv("APEX_FLASHBOTS_SIGNING_KEY")

    def _sign_payload(self, body: str) -> Optional[str]:
        """
        Produce the ``X-Flashbots-Signature`` header value.

        Format: ``<signer_address>:<EIP-191 signature of keccak(body)>``
        Returns ``None`` when no signing key is configured.
        """
        if not self.signing_key:
            return None
        try:
            body_hash = Web3.keccak(text=body).hex()
            message = encode_defunct(text=body_hash)
            signed = Account.sign_message(message, private_key=self.signing_key)
            signer = Account.from_key(self.signing_key).address
            return f"{signer}:{signed.signature.hex()}"
        except Exception as exc:
            logger.warning("Failed to sign Flashbots payload: %s", exc)
            return None

    async def submit(self, bundle: MEVBundle) -> Dict[str, Any]:
        """
        POST the bundle to the MEV relay and return the submission result.

        Returns
        -------
        dict with keys:
          success     : bool
          bundle_hash : str  — relay-assigned bundle identifier
          raw         : dict — full relay response
          error       : str  — error message on failure
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [
                {
                    "txs": [tx.signed_raw_tx for tx in bundle.txs],
                    "blockNumber": hex(bundle.target_block),
                    "minTimestamp": 0,
                    "maxTimestamp": 0,
                    "revertingTxHashes": [],
                }
            ],
        }

        body = json.dumps(payload)
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        signature = self._sign_payload(body)
        if signature:
            headers["X-Flashbots-Signature"] = signature

        timeout = aiohttp.ClientTimeout(total=self._TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.relay_url, data=body, headers=headers, ssl=False
                ) as response:
                    data = await response.json(content_type=None)
        except Exception as exc:
            logger.warning("Bundle submission failed: %s", exc)
            return {"success": False, "bundle_hash": "", "raw": {}, "error": str(exc)}

        bundle_hash = (data.get("result") or {}).get("bundleHash", "")
        if bundle_hash:
            bundle.submission_id = bundle_hash

        return {
            "success": "result" in data and not data.get("error"),
            "bundle_hash": bundle_hash,
            "raw": data,
            "error": str(data["error"]) if data.get("error") else None,
        }
