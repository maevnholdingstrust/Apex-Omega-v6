"""Per-venue protocol adapter modules for Apex-Omega v6.

Each adapter encodes the exact swap calldata for a specific DEX, with:

* Router address resolved from environment variables (with canonical fallbacks).
* Fee tier derived from on-chain pool metadata, not hardcoded.
* Fail-closed behaviour: unknown DEX names raise :exc:`UnknownDexError`.
* Recipient semantics: the executor contract address is always the swap
  recipient so output tokens are deposited where the flash-loan repayment
  logic expects them.

DEX keys (pass these as ``dex_key`` at every call site)
-------------------------------------------------------
* ``"quickswap-v2"``  – QuickSwap V2 (Uniswap V2-compatible router)
* ``"sushi-v2"``      – SushiSwap V2 (Uniswap V2-compatible router)
* ``"uniswap-v3"``    – Uniswap V3 ``exactInputSingle``
* ``"quickswap-v3"``  – QuickSwap V3 / Algebra V3 ``exactInputSingle``
* ``"curve"``         – Curve Router ``exchange``
* ``"balancer"``      – Balancer Vault V2 ``swap``

Fee tier encoding
-----------------
Uniswap V3 and Algebra V3 pool fee tiers are stored as *micro-units*:
``500`` = 0.05 %, ``3000`` = 0.30 %, ``10000`` = 1.00 %.  This matches the
``fee_tier`` field on :class:`~python.apex_omega_core.core.live_data_feeds.PoolReserveSnapshot`
and the Uniswap V3 on-chain representation.  Pass the raw ``fee_tier`` int
and this module converts it appropriately per venue.  Algebra V3 does not
accept a fee parameter in its calldata (the pool itself is fee-dynamic).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from eth_abi import encode as abi_encode
from web3 import Web3

# ---------------------------------------------------------------------------
# Protocol ID constants – mirror python/apex_omega_core/core/protocol_swaps.py
# ---------------------------------------------------------------------------

PROTOCOL_UNISWAP_V2 = 1   # used for all Uni V2-compatible routers
PROTOCOL_UNISWAP_V3 = 2   # Uniswap V3 exactInputSingle
PROTOCOL_ALGEBRA = 3       # QuickSwap V3 / Algebra V3 exactInputSingle
PROTOCOL_CURVE = 4         # Curve Router exchange
PROTOCOL_BALANCER = 5      # Balancer Vault swap

# ---------------------------------------------------------------------------
# Canonical Polygon (chain 137) router addresses
# These are the production-audited defaults; override via env vars below.
# ---------------------------------------------------------------------------

_CANONICAL_ROUTERS: Dict[str, str] = {
    "quickswap-v2": "0xa5E0829CaCED8fFDD4De3c43696c57F7D7A678ff",
    "sushi-v2":     "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    "uniswap-v3":   "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "quickswap-v3": "0xf5b509bB0909a69B1c207E495f687a596C168E12",
    "curve":        "0x0dcded3545d565ba3b19e683431381007245d983",
    "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
}

# Env-var override names per DEX key (checked in order; first non-empty wins).
_ROUTER_ENV_VARS: Dict[str, List[str]] = {
    "quickswap-v2": ["ROUTER_QUICKSWAP", "QUICKSWAP_ROUTER"],
    "sushi-v2":     ["ROUTER_SUSHISWAP", "SUSHISWAP_ROUTER"],
    "uniswap-v3":   ["ROUTER_UNISWAP_V3", "UNISWAP_V3_ROUTER", "SWAP_ROUTER_02"],
    "quickswap-v3": ["ROUTER_QUICKSWAP_V3", "QUICKSWAP_V3_ROUTER"],
    "curve":        ["ROUTER_CURVE", "CURVE_ROUTER"],
    "balancer":     ["BALANCER_VAULT", "BALANCER_VAULT_V2"],
}

# Default swap deadline offset from epoch – effectively unlimited.
_DEADLINE_UNLIMITED = 2**256 - 1

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnknownDexError(ValueError):
    """Raised when an unrecognised DEX key is requested.

    This is intentionally fail-closed: callers must supply an explicit,
    known DEX key; there is no fallback to QuickSwap V2 or any other default.
    """


class SimulationFailedError(RuntimeError):
    """Raised when the required pre-broadcast ``eth_call`` simulation reverts."""


# ---------------------------------------------------------------------------
# Pool fee / router metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolFeeInfo:
    """Resolved metadata for a single pool, used by adapters.

    Parameters
    ----------
    dex_key:
        Canonical DEX identifier (e.g. ``"uniswap-v3"``).
    router_address:
        Checksummed router / vault address for this venue.
    fee_tier:
        Pool fee in micro-units (e.g. 3000 = 0.30 %).  Zero for venues where
        fee is not a calldata parameter (Algebra V3, Curve, Balancer).
    tick_spacing:
        V3 tick spacing corresponding to the fee tier; 0 when not applicable.
    """

    dex_key: str
    router_address: str
    fee_tier: int
    tick_spacing: int = 0


# Mapping from Uniswap V3 fee tier → tick spacing (canonical Polygon values).
_V3_FEE_TICK_SPACING: Dict[int, int] = {
    100:   1,
    500:   10,
    3000:  60,
    10000: 200,
}


def resolve_pool_fee_info(dex_key: str, fee_tier: int) -> PoolFeeInfo:
    """Build :class:`PoolFeeInfo` from a DEX key and raw pool fee tier.

    Parameters
    ----------
    dex_key:
        Canonical DEX key (see module docstring).
    fee_tier:
        Pool fee in micro-units.  For V2/Curve/Balancer this is typically
        ``3000`` or ``0``; it is not used in their calldata.

    Raises
    ------
    UnknownDexError
        When *dex_key* is not in the set of supported venues.
    """
    if dex_key not in _CANONICAL_ROUTERS:
        raise UnknownDexError(
            f"Unknown DEX key {dex_key!r}. "
            f"Supported keys: {sorted(_CANONICAL_ROUTERS)}"
        )
    router = _resolve_router_address(dex_key)
    tick_spacing = _V3_FEE_TICK_SPACING.get(fee_tier, 0)
    return PoolFeeInfo(
        dex_key=dex_key,
        router_address=router,
        fee_tier=fee_tier,
        tick_spacing=tick_spacing,
    )


def _resolve_router_address(dex_key: str) -> str:
    """Return router address: env var → canonical default."""
    for env_var in _ROUTER_ENV_VARS.get(dex_key, []):
        value = os.getenv(env_var, "").strip()
        if value:
            return Web3.to_checksum_address(value)
    default = _CANONICAL_ROUTERS[dex_key]
    return Web3.to_checksum_address(default)


# ---------------------------------------------------------------------------
# Low-level selector helper
# ---------------------------------------------------------------------------


def _selector(signature: str) -> bytes:
    """Return the 4-byte Keccak-256 selector for *signature*."""
    return bytes(Web3.keccak(text=signature)[:4])


# ---------------------------------------------------------------------------
# QuickSwap V2 adapter
# ---------------------------------------------------------------------------


class QuickSwapV2Adapter:
    """QuickSwap V2 ``swapExactTokensForTokens`` encoder."""

    protocol_id: int = PROTOCOL_UNISWAP_V2

    SELECTOR = _selector(
        "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"
    )

    @classmethod
    def encode(
        cls,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        recipient: str,
        *,
        path: Optional[List[str]] = None,
        deadline: int = _DEADLINE_UNLIMITED,
    ) -> bytes:
        """Encode ``swapExactTokensForTokens`` calldata.

        Parameters
        ----------
        token_in / token_out:
            ERC-20 token addresses.
        amount_in:
            Exact input amount in token base units.
        min_amount_out:
            Minimum accepted output amount.
        recipient:
            Address that receives output tokens.  Must be the executor contract.
        path:
            Override token path.  Defaults to ``[token_in, token_out]``.
        deadline:
            Unix timestamp deadline.  Defaults to unlimited.
        """
        _path = path or [token_in, token_out]
        if len(_path) < 2:
            raise ValueError("V2 path must contain at least two tokens")
        if Web3.to_checksum_address(_path[0]) == Web3.to_checksum_address(_path[-1]):
            raise ValueError("V2 path must not start and end with the same token")
        if amount_in <= 0:
            raise ValueError("amount_in must be positive")
        if min_amount_out <= 0:
            raise ValueError("min_amount_out must be positive")
        return cls.SELECTOR + abi_encode(
            ["uint256", "uint256", "address[]", "address", "uint256"],
            [
                int(amount_in),
                int(min_amount_out),
                [Web3.to_checksum_address(t) for t in _path],
                Web3.to_checksum_address(recipient),
                int(deadline),
            ],
        )


# ---------------------------------------------------------------------------
# SushiSwap V2 adapter (identical ABI, different router)
# ---------------------------------------------------------------------------


class SushiV2Adapter(QuickSwapV2Adapter):
    """SushiSwap V2 – same ABI as QuickSwap V2, separate router address."""

    # Inherits QuickSwapV2Adapter.encode() verbatim; the router address is
    # resolved by the caller via resolve_pool_fee_info("sushi-v2", ...).


# ---------------------------------------------------------------------------
# Uniswap V3 adapter
# ---------------------------------------------------------------------------


class UniswapV3Adapter:
    """Uniswap V3 ``exactInputSingle`` encoder."""

    protocol_id: int = PROTOCOL_UNISWAP_V3

    SELECTOR = _selector(
        "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
    )

    @classmethod
    def encode(
        cls,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        recipient: str,
        fee_tier: int,
        *,
        deadline: int = _DEADLINE_UNLIMITED,
        sqrt_price_limit_x96: int = 0,
    ) -> bytes:
        """Encode ``exactInputSingle`` calldata.

        Parameters
        ----------
        fee_tier:
            Pool fee in micro-units (e.g. 500, 3000, 10000).  Derived from
            pool metadata — not hardcoded.
        """
        if fee_tier <= 0:
            raise ValueError("Uniswap V3 fee_tier must be positive")
        params = (
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(fee_tier),
            Web3.to_checksum_address(recipient),
            int(deadline),
            int(amount_in),
            int(min_amount_out),
            int(sqrt_price_limit_x96),
        )
        return cls.SELECTOR + abi_encode(
            ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
            [params],
        )


# ---------------------------------------------------------------------------
# QuickSwap V3 / Algebra V3 adapter
# ---------------------------------------------------------------------------


class AlgebraV3Adapter:
    """QuickSwap V3 / Algebra ``exactInputSingle`` encoder.

    Algebra V3 omits the ``fee`` parameter; fees are dynamic per pool.
    The struct layout is:
    ``(address tokenIn, address tokenOut, address recipient, uint256 deadline,
       uint256 amountIn, uint256 amountOutMinimum, uint160 limitSqrtPrice)``
    """

    protocol_id: int = PROTOCOL_ALGEBRA

    SELECTOR = _selector(
        "exactInputSingle((address,address,address,uint256,uint256,uint256,uint160))"
    )

    @classmethod
    def encode(
        cls,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        recipient: str,
        *,
        deadline: int = _DEADLINE_UNLIMITED,
        limit_sqrt_price: int = 0,
    ) -> bytes:
        """Encode Algebra V3 ``exactInputSingle`` calldata."""
        params = (
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            Web3.to_checksum_address(recipient),
            int(deadline),
            int(amount_in),
            int(min_amount_out),
            int(limit_sqrt_price),
        )
        return cls.SELECTOR + abi_encode(
            ["(address,address,address,uint256,uint256,uint256,uint160)"],
            [params],
        )


# ---------------------------------------------------------------------------
# Curve adapter
# ---------------------------------------------------------------------------


class CurveAdapter:
    """Curve Router ``exchange`` encoder.

    Targets the Polygon Curve Router at
    ``0x0dcded3545d565ba3b19e683431381007245d983`` (overridable via env).

    Signature:
    ``exchange(address _pool, address _from, address _to,
                uint256 _amount, uint256 _expected, address _receiver)``
    """

    protocol_id: int = PROTOCOL_CURVE

    SELECTOR = _selector(
        "exchange(address,address,address,uint256,uint256,address)"
    )

    @classmethod
    def encode(
        cls,
        pool: str,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        recipient: str,
    ) -> bytes:
        """Encode ``exchange`` calldata.

        Parameters
        ----------
        pool:
            Address of the Curve pool being traded through.
        recipient:
            Receiver of output tokens.  Must be the executor contract.
        """
        return cls.SELECTOR + abi_encode(
            ["address", "address", "address", "uint256", "uint256", "address"],
            [
                Web3.to_checksum_address(pool),
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                int(amount_in),
                int(min_amount_out),
                Web3.to_checksum_address(recipient),
            ],
        )


# ---------------------------------------------------------------------------
# Balancer adapter
# ---------------------------------------------------------------------------


class BalancerAdapter:
    """Balancer Vault V2 single-swap ``swap`` encoder.

    Signature:
    ``swap((bytes32,uint8,address,address,uint256,bytes),(address,bool,address,bool),uint256,uint256)``
    """

    protocol_id: int = PROTOCOL_BALANCER

    SELECTOR = _selector(
        "swap((bytes32,uint8,address,address,uint256,bytes),(address,bool,address,bool),uint256,uint256)"
    )

    @classmethod
    def encode(
        cls,
        pool_id: bytes,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        sender: str,
        recipient: str,
        *,
        swap_kind: int = 0,
        user_data: bytes = b"",
        deadline: int = _DEADLINE_UNLIMITED,
        from_internal_balance: bool = False,
        to_internal_balance: bool = False,
    ) -> bytes:
        """Encode Balancer Vault ``swap`` calldata.

        Parameters
        ----------
        pool_id:
            Exactly 32-byte Balancer pool ID.
        sender:
            Source of input tokens (typically the executor contract).
        recipient:
            Destination of output tokens (must be the executor contract).
        swap_kind:
            ``0`` = GIVEN_IN (exactInput), ``1`` = GIVEN_OUT.
        """
        if len(pool_id) != 32:
            raise ValueError(f"Balancer pool_id must be exactly 32 bytes, got {len(pool_id)}")
        single_swap = (
            pool_id,
            int(swap_kind),
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(amount_in),
            bytes(user_data),
        )
        fund_management = (
            Web3.to_checksum_address(sender),
            bool(from_internal_balance),
            Web3.to_checksum_address(recipient),
            bool(to_internal_balance),
        )
        return cls.SELECTOR + abi_encode(
            [
                "(bytes32,uint8,address,address,uint256,bytes)",
                "(address,bool,address,bool)",
                "uint256",
                "uint256",
            ],
            [single_swap, fund_management, int(min_amount_out), int(deadline)],
        )


# ---------------------------------------------------------------------------
# Adapter registry + dispatch
# ---------------------------------------------------------------------------

#: Maps DEX key → adapter class.
_ADAPTER_REGISTRY: Dict[str, Any] = {
    "quickswap-v2": QuickSwapV2Adapter,
    "sushi-v2":     SushiV2Adapter,
    "uniswap-v3":   UniswapV3Adapter,
    "quickswap-v3": AlgebraV3Adapter,
    "curve":        CurveAdapter,
    "balancer":     BalancerAdapter,
}


def get_adapter(dex_key: str) -> Any:
    """Return the adapter class for *dex_key*.

    Raises
    ------
    UnknownDexError
        When *dex_key* is not a recognised venue.  This is the fail-closed
        guard: callers must supply an explicit key — no silent defaults.
    """
    adapter = _ADAPTER_REGISTRY.get(dex_key)
    if adapter is None:
        raise UnknownDexError(
            f"No adapter registered for DEX key {dex_key!r}. "
            f"Supported: {sorted(_ADAPTER_REGISTRY)}"
        )
    return adapter


def encode_swap_step(
    dex_key: str,
    pool_fee_info: PoolFeeInfo,
    step_params: Mapping[str, Any],
) -> bytes:
    """Encode a single swap step for the given venue.

    Parameters
    ----------
    dex_key:
        Canonical DEX key.
    pool_fee_info:
        Resolved pool / router metadata (from :func:`resolve_pool_fee_info`).
    step_params:
        Venue-specific parameters dict.  Required keys vary per adapter but
        the following are always expected:

        * ``token_in``, ``token_out`` – token addresses
        * ``amount_in`` – exact input amount
        * ``min_amount_out`` – minimum output amount
        * ``recipient`` – swap output recipient (executor contract address)

    Raises
    ------
    UnknownDexError
        For an unrecognised *dex_key*.
    KeyError
        When a required parameter is absent from *step_params*.
    """
    adapter = get_adapter(dex_key)

    token_in = step_params["token_in"]
    token_out = step_params["token_out"]
    amount_in = int(step_params["amount_in"])
    min_amount_out = int(step_params["min_amount_out"])
    recipient = step_params["recipient"]

    if dex_key in ("quickswap-v2", "sushi-v2"):
        return adapter.encode(
            token_in,
            token_out,
            amount_in,
            min_amount_out,
            recipient,
            path=step_params.get("path"),
            deadline=int(step_params.get("deadline", _DEADLINE_UNLIMITED)),
        )

    if dex_key == "uniswap-v3":
        return adapter.encode(
            token_in,
            token_out,
            amount_in,
            min_amount_out,
            recipient,
            pool_fee_info.fee_tier,
            deadline=int(step_params.get("deadline", _DEADLINE_UNLIMITED)),
            sqrt_price_limit_x96=int(step_params.get("sqrt_price_limit_x96", 0)),
        )

    if dex_key == "quickswap-v3":
        return adapter.encode(
            token_in,
            token_out,
            amount_in,
            min_amount_out,
            recipient,
            deadline=int(step_params.get("deadline", _DEADLINE_UNLIMITED)),
            limit_sqrt_price=int(step_params.get("limit_sqrt_price", 0)),
        )

    if dex_key == "curve":
        return adapter.encode(
            step_params["pool"],
            token_in,
            token_out,
            amount_in,
            min_amount_out,
            recipient,
        )

    if dex_key == "balancer":
        raw_pool_id = step_params["pool_id"]
        if isinstance(raw_pool_id, str) and raw_pool_id.startswith("0x"):
            pool_id_bytes = bytes.fromhex(raw_pool_id[2:])
        else:
            pool_id_bytes = bytes(raw_pool_id)
        return adapter.encode(
            pool_id_bytes,
            token_in,
            token_out,
            amount_in,
            min_amount_out,
            sender=step_params.get("sender", recipient),
            recipient=recipient,
            swap_kind=int(step_params.get("swap_kind", 0)),
            user_data=bytes(step_params.get("user_data", b"")),
            deadline=int(step_params.get("deadline", _DEADLINE_UNLIMITED)),
        )

    # Should be unreachable because get_adapter() already fails closed, but
    # defensive just in case the registry and the dispatch diverge.
    raise UnknownDexError(f"No encode branch for DEX key {dex_key!r}")


__all__ = [
    "PROTOCOL_UNISWAP_V2",
    "PROTOCOL_UNISWAP_V3",
    "PROTOCOL_ALGEBRA",
    "PROTOCOL_CURVE",
    "PROTOCOL_BALANCER",
    "PoolFeeInfo",
    "UnknownDexError",
    "SimulationFailedError",
    "QuickSwapV2Adapter",
    "SushiV2Adapter",
    "UniswapV3Adapter",
    "AlgebraV3Adapter",
    "CurveAdapter",
    "BalancerAdapter",
    "resolve_pool_fee_info",
    "get_adapter",
    "encode_swap_step",
]
