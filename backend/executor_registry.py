"""Canonical executor contract registry for Apex-Omega v6.

This module is the **single source of truth** for all on-chain executor
contract metadata.  Every other backend module (contract_interface,
institutional_executor, liquidation_executor_contract, live_executor,
server) imports from here instead of embedding addresses, ABIs, or
function signatures directly.

Registry structure
------------------
``SUPPORTED_CHAINS``
    Maps EIP-155 chain ID → :class:`ChainConfig`.

``EXECUTOR_REGISTRY``
    Maps ``(chain_id, strategy)`` → :class:`ExecutorEntry`.

Startup validation
------------------
Call :func:`validate_registry_entry` (or :func:`validate_all`) at
process startup to confirm that:

* the configured address has non-empty deployed bytecode,
* every registered function selector is present in that bytecode,
* the node's reported chain ID matches the registry entry,
* the configured executor wallet is the contract owner.

All checks require a live RPC connection; they are skipped in dry-run
mode and when the executor address is not configured.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from web3 import Web3 as _Web3
except ImportError:  # pragma: no cover – web3 optional at import time
    _Web3 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strategy identifiers
# ---------------------------------------------------------------------------

STRATEGY_C1 = "institutional"
STRATEGY_C2 = "ultimate"

# ---------------------------------------------------------------------------
# Chain configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainConfig:
    """Static configuration for a supported EVM chain."""

    chain_id: int
    name: str
    rpc_env_var: str
    native_symbol: str
    block_time_s: float = 2.0  # approximate seconds per block


SUPPORTED_CHAINS: Dict[int, ChainConfig] = {
    137: ChainConfig(
        chain_id=137,
        name="Polygon",
        rpc_env_var="POLYGON_RPC",
        native_symbol="POL",
        block_time_s=2.0,
    ),
    1: ChainConfig(
        chain_id=1,
        name="Ethereum",
        rpc_env_var="ETH_RPC",
        native_symbol="ETH",
        block_time_s=12.0,
    ),
}

# ---------------------------------------------------------------------------
# ABI definitions
# ---------------------------------------------------------------------------
# Minimal inline ABIs covering every function and event that the backend
# modules invoke.  Full ABIs for Solidity verification are compiled from
# contracts/InstitutionalExecutor.sol and contracts/UltimateArbitrageExecutor.sol.
# The "abi_id" string in ExecutorEntry keys into INLINE_ABIS.

INLINE_ABIS: Dict[str, List[Dict[str, Any]]] = {
    "institutional_executor": [
        # ── entry points ──────────────────────────────────────────────
        {
            "type": "function",
            "name": "initAaveFlash",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "asset",      "type": "address"},
                {"name": "amount",     "type": "uint256"},
                {"name": "minProfit",  "type": "uint256"},
                {"name": "payload",    "type": "bytes"},
            ],
            "outputs": [],
        },
        {
            "type": "function",
            "name": "initBalancerFlash",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "asset",      "type": "address"},
                {"name": "amount",     "type": "uint256"},
                {"name": "minProfit",  "type": "uint256"},
                {"name": "payload",    "type": "bytes"},
            ],
            "outputs": [],
        },
        # ── callbacks ─────────────────────────────────────────────────
        {
            "type": "function",
            "name": "executeOperation",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "asset",      "type": "address"},
                {"name": "amount",     "type": "uint256"},
                {"name": "premium",    "type": "uint256"},
                {"name": "initiator",  "type": "address"},
                {"name": "params",     "type": "bytes"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "type": "function",
            "name": "unlockCallback",
            "stateMutability": "nonpayable",
            "inputs": [{"name": "data", "type": "bytes"}],
            "outputs": [{"name": "", "type": "bytes"}],
        },
        # ── admin ─────────────────────────────────────────────────────
        {
            "type": "function",
            "name": "approveRouter",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "token",   "type": "address"},
                {"name": "router",  "type": "address"},
                {"name": "amount",  "type": "uint256"},
            ],
            "outputs": [],
        },
        {
            "type": "function",
            "name": "rescueToken",
            "stateMutability": "nonpayable",
            "inputs": [{"name": "token", "type": "address"}],
            "outputs": [],
        },
        # ── view ──────────────────────────────────────────────────────
        {
            "type": "function",
            "name": "owner",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "address"}],
        },
        # ── events ────────────────────────────────────────────────────
        {
            "type": "event",
            "name": "FlashArbExecuted",
            "inputs": [
                {"name": "asset",   "type": "address", "indexed": True},
                {"name": "amount",  "type": "uint256", "indexed": False},
                {"name": "profit",  "type": "uint256", "indexed": False},
            ],
        },
        {
            "type": "event",
            "name": "RouteEnvelopeExecuted",
            "inputs": [
                {"name": "version",     "type": "uint8",    "indexed": False},
                {"name": "steps",       "type": "uint256",  "indexed": False},
                {"name": "profitToken", "type": "address",  "indexed": True},
            ],
        },
    ],
    "ultimate_arbitrage_executor": [
        # ── entry points ──────────────────────────────────────────────
        {
            "type": "function",
            "name": "executeArbitrage",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "asset",       "type": "address"},
                {"name": "amount",      "type": "uint256"},
                {"name": "minProfit",   "type": "uint256"},
                {"name": "merkleProof", "type": "bytes32[]"},
                {"name": "payload",     "type": "bytes"},
            ],
            "outputs": [],
        },
        {
            "type": "function",
            "name": "executeOperation",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "assets",            "type": "address[]"},
                {"name": "amounts",           "type": "uint256[]"},
                {"name": "premiums",          "type": "uint256[]"},
                {"name": "initiator",         "type": "address"},
                {"name": "params",            "type": "bytes"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "type": "function",
            "name": "receiveFlashLoan",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "tokens",      "type": "address[]"},
                {"name": "amounts",     "type": "uint256[]"},
                {"name": "feeAmounts",  "type": "uint256[]"},
                {"name": "userData",    "type": "bytes"},
            ],
            "outputs": [],
        },
        # ── admin ─────────────────────────────────────────────────────
        {
            "type": "function",
            "name": "rescueToken",
            "stateMutability": "nonpayable",
            "inputs": [{"name": "token", "type": "address"}],
            "outputs": [],
        },
        # ── view ──────────────────────────────────────────────────────
        {
            "type": "function",
            "name": "owner",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "address"}],
        },
    ],
}

# ---------------------------------------------------------------------------
# Function signatures per strategy
# ---------------------------------------------------------------------------
# These are the canonical Solidity function signatures used to compute
# the 4-byte selectors.  They are also used by startup validation to
# confirm each selector appears in the deployed bytecode.

FUNCTION_SIGNATURES: Dict[str, List[str]] = {
    STRATEGY_C1: [
        "initAaveFlash(address,uint256,uint256,bytes)",
        "initBalancerFlash(address,uint256,uint256,bytes)",
        "executeOperation(address,uint256,uint256,address,bytes)",
        "unlockCallback(bytes)",
        "approveRouter(address,address,uint256)",
        "rescueToken(address)",
        "owner()",
    ],
    STRATEGY_C2: [
        "executeArbitrage(address,uint256,uint256,bytes32[],bytes)",
        "executeOperation(address[],uint256[],uint256[],address,bytes)",
        "receiveFlashLoan(address[],uint256[],uint256[],bytes)",
        "rescueToken(address)",
        "owner()",
    ],
}

# ---------------------------------------------------------------------------
# Deployment status
# ---------------------------------------------------------------------------


class DeploymentStatus(str, Enum):
    DEPLOYED = "deployed"
    PENDING = "pending"
    UNDEPLOYED = "undeployed"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Executor entry
# ---------------------------------------------------------------------------


@dataclass
class ExecutorEntry:
    """All registry metadata for a single deployed executor contract.

    Parameters
    ----------
    chain_id:
        EIP-155 chain identifier.
    strategy:
        One of :data:`STRATEGY_C1` or :data:`STRATEGY_C2`.
    address_env_var:
        Name of the environment variable that holds the deployed address.
        This makes the registry fork-safe: the value is resolved lazily so
        that CI without secrets still imports the module without error.
    abi_id:
        Key into :data:`INLINE_ABIS`.
    function_signatures:
        Canonical Solidity function signatures for this strategy.
    owner_env_var:
        Name of the env var that holds the expected owner / operator address
        (i.e. the executor wallet).
    required_permissions:
        Human-readable description of on-chain access rights the wallet needs.
    deployment_status:
        Operational status flag.
    deployment_block:
        Block number at which the contract was deployed, or ``None`` if
        unknown.
    fallback_address:
        Hard-coded canonical address used when the env var is absent.
        Should only be set for well-audited, production contracts.
    """

    chain_id: int
    strategy: str
    address_env_var: str
    abi_id: str
    function_signatures: List[str]
    owner_env_var: str
    required_permissions: List[str]
    deployment_status: DeploymentStatus = DeploymentStatus.UNKNOWN
    deployment_block: Optional[int] = None
    fallback_address: Optional[str] = None

    # ── derived helpers ───────────────────────────────────────────────────

    @property
    def address(self) -> str:
        """Resolve the executor address from the environment.

        Returns the configured address (env var → fallback) or an empty
        string when neither is set.
        """
        return os.getenv(self.address_env_var, "") or self.fallback_address or ""

    @property
    def owner_address(self) -> str:
        """Resolve the expected owner address from the environment."""
        return os.getenv(self.owner_env_var, "")

    @property
    def abi(self) -> List[Dict[str, Any]]:
        """Return the inline ABI for this entry."""
        return INLINE_ABIS[self.abi_id]

    @property
    def selectors(self) -> List[bytes]:
        """Return the 4-byte function selectors for all registered signatures."""
        return [_keccak4(sig) for sig in self.function_signatures]

    def as_dict(self) -> Dict[str, Any]:
        """Serialisable representation (safe for API responses / logging)."""
        return {
            "chain_id": self.chain_id,
            "strategy": self.strategy,
            "address": self.address,
            "abi_id": self.abi_id,
            "function_signatures": self.function_signatures,
            "owner_address": self.owner_address,
            "required_permissions": self.required_permissions,
            "deployment_status": self.deployment_status.value,
            "deployment_block": self.deployment_block,
        }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

EXECUTOR_REGISTRY: Dict[Tuple[int, str], ExecutorEntry] = {
    # ── Polygon – C1 (InstitutionalExecutor) ──────────────────────────────
    (137, STRATEGY_C1): ExecutorEntry(
        chain_id=137,
        strategy=STRATEGY_C1,
        address_env_var="C1_INSTITUTIONAL_EXECUTOR_ADDRESS",
        abi_id="institutional_executor",
        function_signatures=FUNCTION_SIGNATURES[STRATEGY_C1],
        owner_env_var="OPERATOR_ADDRESS",
        required_permissions=[
            "owner() == OPERATOR_ADDRESS",
            "Can call initAaveFlash and initBalancerFlash",
        ],
        deployment_status=DeploymentStatus.DEPLOYED,
        deployment_block=None,
        fallback_address="0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD",
    ),
    # ── Polygon – C2 (UltimateArbitrageExecutor) ──────────────────────────
    (137, STRATEGY_C2): ExecutorEntry(
        chain_id=137,
        strategy=STRATEGY_C2,
        address_env_var="C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS",
        abi_id="ultimate_arbitrage_executor",
        function_signatures=FUNCTION_SIGNATURES[STRATEGY_C2],
        owner_env_var="OPERATOR_ADDRESS",
        required_permissions=[
            "owner() == OPERATOR_ADDRESS",
            "Can call executeArbitrage",
        ],
        deployment_status=DeploymentStatus.DEPLOYED,
        deployment_block=None,
        fallback_address="0x0466759822ABAA7E416276E1cf2b538d7FC540BD",
    ),
}

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def get_entry(chain_id: int, strategy: str) -> ExecutorEntry:
    """Return the registry entry for *(chain_id, strategy)*.

    Raises
    ------
    KeyError
        When no entry exists for the requested combination.
    """
    key = (chain_id, strategy)
    if key not in EXECUTOR_REGISTRY:
        raise KeyError(
            f"No executor registry entry for chain_id={chain_id}, strategy={strategy!r}. "
            f"Known entries: {list(EXECUTOR_REGISTRY)}"
        )
    return EXECUTOR_REGISTRY[key]


def get_rpc_url(chain_id: int) -> str:
    """Return the RPC URL for *chain_id* from the environment.

    Falls back to ``https://polygon-rpc.com/`` for Polygon when the env
    var is absent, and raises :exc:`ValueError` for unsupported chains.
    """
    chain = SUPPORTED_CHAINS.get(chain_id)
    if chain is None:
        raise ValueError(f"Unsupported chain_id={chain_id}")
    url = os.getenv(chain.rpc_env_var, "")
    if not url and chain_id == 137:
        url = "https://polygon-rpc.com/"
    return url


def list_entries() -> List[ExecutorEntry]:
    """Return all registry entries as a list."""
    return list(EXECUTOR_REGISTRY.values())


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _keccak4(signature: str) -> bytes:
    """Return the first 4 bytes of the Keccak-256 hash of *signature*.

    Requires ``web3`` to be installed; raises :exc:`RuntimeError` when
    it is absent because the SHA-3/Keccak-256 distinction means the
    hashlib fallback would produce incorrect function selectors.
    """
    if _Web3 is None:
        raise RuntimeError(
            "web3 package is required for selector computation but is not installed. "
            "Install it with: pip install web3"
        )
    return bytes(_Web3.keccak(text=signature)[:4])


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Outcome of :func:`validate_registry_entry`."""

    chain_id: int
    strategy: str
    address: str
    passed: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "strategy": self.strategy,
            "address": self.address,
            "passed": self.passed,
            "checks": self.checks,
            "errors": self.errors,
        }


def validate_registry_entry(
    entry: ExecutorEntry,
    *,
    rpc_url: Optional[str] = None,
) -> ValidationResult:
    """Run startup validation for a single registry entry.

    Checks performed
    ----------------
    1. **address_configured** – entry.address is non-empty.
    2. **bytecode_exists** – ``eth_getCode`` returns non-empty bytecode at
       the configured address.
    3. **selectors_present** – every 4-byte function selector derived from
       the registered signatures is found somewhere in the bytecode.
    4. **chain_id_matches** – ``eth_chainId`` on the node equals
       ``entry.chain_id``.
    5. **wallet_authorized** – if ``entry.owner_address`` is non-empty,
       ``owner()`` on the contract equals the configured operator address
       (case-insensitive).

    All checks require a live RPC connection.  When the RPC is
    unreachable or the address is unconfigured the result is marked as
    not passed and the reason is recorded in ``errors``.

    Parameters
    ----------
    entry:
        Registry entry to validate.
    rpc_url:
        Override RPC URL.  Defaults to :func:`get_rpc_url` for the
        entry's chain ID.

    Returns
    -------
    ValidationResult
        Never raises; all errors are captured in the result object.
    """
    result = ValidationResult(
        chain_id=entry.chain_id,
        strategy=entry.strategy,
        address=entry.address,
        passed=False,
        checks={},
        errors=[],
    )

    # ── check 1: address configured ──────────────────────────────────────
    if not entry.address:
        result.checks["address_configured"] = False
        result.errors.append(
            f"Executor address not configured. "
            f"Set env var {entry.address_env_var!r}."
        )
        return result
    result.checks["address_configured"] = True

    # ── acquire Web3 ─────────────────────────────────────────────────────
    if _Web3 is None:
        result.errors.append("web3 package not installed; cannot run on-chain validation.")
        return result

    Web3 = _Web3

    try:
        url = rpc_url or get_rpc_url(entry.chain_id)
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
    except Exception as exc:
        logger.debug("Failed to create Web3 provider for chain %d: %s", entry.chain_id, exc)
        result.errors.append("Failed to create RPC provider. Check network configuration.")
        return result

    address = Web3.to_checksum_address(entry.address)

    # ── check 2: bytecode exists ──────────────────────────────────────────
    try:
        code: bytes = w3.eth.get_code(address)
        has_bytecode = len(code) > 0
        result.checks["bytecode_exists"] = has_bytecode
        if not has_bytecode:
            result.errors.append(
                f"No bytecode at {address} on chain {entry.chain_id}. "
                "Contract may not be deployed."
            )
            return result
    except Exception as exc:
        logger.debug("eth_getCode failed for %s on chain %d: %s", address, entry.chain_id, exc)
        result.checks["bytecode_exists"] = False
        result.errors.append("Failed to retrieve contract bytecode. Check RPC connectivity and contract address.")
        return result

    # ── check 3: function selectors present in bytecode ───────────────────
    selector_ok = True
    for sig in entry.function_signatures:
        sel = _keccak4(sig)
        found = sel in code
        result.checks[f"selector:{sig[:sig.index('(')]}"] = found
        if not found:
            selector_ok = False
            result.errors.append(
                f"Function selector for {sig!r} ({sel.hex()}) not found in deployed bytecode."
            )
    result.checks["selectors_present"] = selector_ok

    # ── check 4: chain ID matches ─────────────────────────────────────────
    try:
        node_chain_id = int(w3.eth.chain_id)
        chain_ok = node_chain_id == entry.chain_id
        result.checks["chain_id_matches"] = chain_ok
        if not chain_ok:
            result.errors.append(
                f"Chain ID mismatch: registry expects {entry.chain_id}, "
                f"node reports {node_chain_id}."
            )
    except Exception as exc:
        logger.debug("eth_chainId call failed for chain %d: %s", entry.chain_id, exc)
        result.checks["chain_id_matches"] = False
        result.errors.append("Failed to retrieve chain ID from RPC node.")

    # ── check 5: wallet authorized (owner check) ──────────────────────────
    if entry.owner_address:
        try:
            owner_selector = _keccak4("owner()")
            call_result: bytes = w3.eth.call(
                {"to": address, "data": "0x" + owner_selector.hex()}
            )
            # owner() returns a padded address (32 bytes); last 20 are the address
            if len(call_result) >= 32:
                contract_owner = Web3.to_checksum_address(
                    "0x" + call_result[-20:].hex()
                )
                expected = Web3.to_checksum_address(entry.owner_address)
                authorized = contract_owner.lower() == expected.lower()
                result.checks["wallet_authorized"] = authorized
                if not authorized:
                    result.errors.append(
                        f"Wallet authorization failed: contract owner is "
                        f"{contract_owner}, expected {expected}."
                    )
            else:
                result.checks["wallet_authorized"] = False
                result.errors.append("owner() returned unexpected data.")
        except Exception as exc:
            logger.debug("owner() call failed for %s: %s", address, exc)
            result.checks["wallet_authorized"] = False
            result.errors.append("Failed to call owner() on contract.")
    else:
        result.checks["wallet_authorized"] = None  # type: ignore[assignment]
        logger.debug(
            "Skipping wallet authorization check for %s/%s: "
            "OPERATOR_ADDRESS env var not set.",
            entry.chain_id,
            entry.strategy,
        )

    result.passed = not result.errors
    return result


def validate_all(
    *,
    chain_id: Optional[int] = None,
    rpc_url: Optional[str] = None,
) -> List[ValidationResult]:
    """Run :func:`validate_registry_entry` for every entry in the registry.

    Parameters
    ----------
    chain_id:
        When provided only entries on this chain are validated.
    rpc_url:
        Override RPC URL passed to every validation call.

    Returns
    -------
    list[ValidationResult]
        One result per validated entry.  Logs a summary at INFO level.
    """
    results: List[ValidationResult] = []
    for (cid, strategy), entry in EXECUTOR_REGISTRY.items():
        if chain_id is not None and cid != chain_id:
            continue
        res = validate_registry_entry(entry, rpc_url=rpc_url)
        results.append(res)
        level = logging.INFO if res.passed else logging.WARNING
        logger.log(
            level,
            "Registry validation [chain=%d strategy=%s addr=%s]: %s",
            cid,
            strategy,
            entry.address or "(not configured)",
            "PASS" if res.passed else f"FAIL – {'; '.join(res.errors)}",
        )
    return results
