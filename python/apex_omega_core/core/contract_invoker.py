from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Mapping, Optional

from eth_abi import encode
from web3 import Web3

from .execution_state_store import chain_name_for, explorer_url_for, get_execution_state_store
from .mev_gas_oracle import GasOracle, TipOptimizer
from .telegram_notifier import EventNotifierBase, build_notifier

logger = logging.getLogger(__name__)

# Bounded thread pool for fire-and-forget lifecycle notifications.
# A single worker thread is sufficient; the queue provides back-pressure so
# a slow transport (Telegram) never spins up unbounded threads.
_NOTIFY_POOL: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apex-notify")

# ---------------------------------------------------------------------------
# Unit-conversion helpers (Patches 1 & 2)
# ---------------------------------------------------------------------------

#: Chain-native token USD price used to convert a USD profit amount into Wei
#: for the ``min_profit_wei`` field sent to MEV relays.
#:
#: Keyed by EIP-155 chain ID.  POL (the native token of Polygon, chain 137)
#: replaced MATIC as the gas token following the September 2024 migration.
#:
#: Values are read from the ``APEX_POL_USD`` and ``APEX_ETH_USD``
#: environment variables at module load time so that operators can override
#: them without a code change.  When the env vars are absent the defaults
#: (``0.85`` POL / ``3500`` ETH) are used as a safe fallback.
#:
#: TODO: replace with a live chain-native price oracle lookup so that the
#:       values stay current automatically.
_NATIVE_USD_BY_CHAIN: Dict[int, Decimal] = {
    1: Decimal(os.getenv("APEX_ETH_USD", "3500")),   # ETH on Ethereum mainnet
    137: Decimal(os.getenv("APEX_POL_USD", "0.85")), # POL on Polygon mainnet
}


@dataclass(frozen=True)
class TokenUnitSpec:
    """Minimal token metadata needed for USD → base-unit conversion."""
    symbol: str
    decimals: int
    usd_price: Optional[Decimal] = None


def _to_base_units(amount_tokens: Decimal | float | int | str, decimals: int) -> int:
    """Convert a token amount expressed in human-readable units to integer base units.

    Example: ``_to_base_units(50_000, 6)`` → ``50_000_000_000`` (USDC-style).
    """
    q = Decimal(str(amount_tokens)) * (Decimal(10) ** decimals)
    return int(q.quantize(Decimal("1"), rounding=ROUND_DOWN))


def _usd_to_token_base_units(amount_usd: Decimal | float | int | str, token: TokenUnitSpec) -> int:
    """Convert a USD amount to integer token base units using the token's USD price.

    Raises ``ValueError`` when the token has no valid ``usd_price``.
    """
    if token.usd_price is None or token.usd_price <= 0:
        raise ValueError(f"Missing or invalid usd_price for token {token.symbol!r}")
    token_amount = Decimal(str(amount_usd)) / token.usd_price
    return _to_base_units(token_amount, token.decimals)


def _require_int_base_units(context: Mapping[str, Any], key: str) -> int:
    """Return a non-negative integer base-unit value from *context[key]*.

    Raises ``KeyError`` when the key is absent and ``ValueError`` when the
    resolved value is negative.
    """
    value = context.get(key)
    if value is None:
        raise KeyError(f"Missing required base-unit field: {key!r}")
    ivalue = int(value)
    if ivalue < 0:
        raise ValueError(f"Negative base-unit value for {key!r}: {ivalue}")
    return ivalue


# Keys needed for each resolution path (used by the pre-flight validator below).
_OPTIMAL_INPUT_DIRECT_KEY = "optimal_input_base_units"
_OPTIMAL_INPUT_USD_KEYS = frozenset(
    {"optimal_input", "flashloan_asset_symbol", "flashloan_asset_decimals", "flashloan_asset_usd_price"}
)
_FINAL_OUTPUT_DIRECT_KEY = "min_final_output_base_units"
_FINAL_OUTPUT_USD_KEYS = frozenset(
    {"final_output", "profit_token_symbol", "profit_token_decimals", "profit_token_usd_price"}
)


def _validate_calldata_context(context: Mapping[str, Any]) -> None:
    """Raise a clear :exc:`ValueError` when *context* cannot satisfy either resolution path.

    ``build_c1_calldata`` and ``build_c2_calldata`` call this before invoking
    :func:`resolve_optimal_input_units` / :func:`resolve_min_final_output_units`
    so that callers get an actionable error instead of an opaque ``KeyError``.

    **Context requirements — choose one path per field:**

    *Input amount*

    * Preferred:  ``optimal_input_base_units`` (integer token base units, e.g. 50 000 × 10⁶ for USDC)
    * Fallback:   ``optimal_input`` (USD float) **+** ``flashloan_asset_symbol``,
      ``flashloan_asset_decimals``, ``flashloan_asset_usd_price``

    *Output amount*

    * Preferred:  ``min_final_output_base_units`` (integer token base units)
    * Fallback:   ``final_output`` (USD float) **+** ``profit_token_symbol``,
      ``profit_token_decimals``, ``profit_token_usd_price``

    The easiest fix is to have C1/C2 populate ``optimal_input_base_units`` and
    ``min_final_output_base_units`` in the sentinel output.  If only USD is
    available, add ``flashloan_asset_*`` / ``profit_token_*`` token metadata
    alongside the USD values.

    ``SlippageSentinel`` outputs ``optimal_input`` / ``final_output`` as USD
    floats; these alone are **not sufficient** — token metadata must be added
    upstream or the pre-computed base-unit keys must be attached.
    """
    errors: list[str] = []

    # ── input amount ──────────────────────────────────────────────────────────
    has_input_direct = _OPTIMAL_INPUT_DIRECT_KEY in context
    missing_input_usd = _OPTIMAL_INPUT_USD_KEYS - context.keys()
    if not has_input_direct and missing_input_usd:
        errors.append(
            f"Cannot resolve optimal_input to base units. "
            f"Provide '{_OPTIMAL_INPUT_DIRECT_KEY}' (preferred) or all of "
            f"{sorted(_OPTIMAL_INPUT_USD_KEYS)} for USD conversion. "
            f"Missing keys: {sorted(missing_input_usd)}."
        )

    # ── output amount ─────────────────────────────────────────────────────────
    has_output_direct = _FINAL_OUTPUT_DIRECT_KEY in context
    missing_output_usd = _FINAL_OUTPUT_USD_KEYS - context.keys()
    if not has_output_direct and missing_output_usd:
        errors.append(
            f"Cannot resolve min_final_output to base units. "
            f"Provide '{_FINAL_OUTPUT_DIRECT_KEY}' (preferred) or all of "
            f"{sorted(_FINAL_OUTPUT_USD_KEYS)} for USD conversion. "
            f"Missing keys: {sorted(missing_output_usd)}."
        )

    if errors:
        raise ValueError(
            "Calldata context is missing required fields for token-native unit resolution. "
            + " | ".join(errors)
        )


def resolve_optimal_input_units(context: Mapping[str, Any]) -> int:
    """Resolve the flash-loan input size in token base units.

    Prefers the explicit ``optimal_input_base_units`` key.  Falls back to a
    USD → base-unit conversion using ``flashloan_asset_*`` metadata keys when
    that key is absent (transitional path for callers that still emit USD).

    Required context keys (explicit path):
        ``optimal_input_base_units`` – integer base units

    Required context keys (USD fallback path):
        ``optimal_input``             – USD amount (float)
        ``flashloan_asset_symbol``    – token symbol string
        ``flashloan_asset_decimals``  – token decimals (int)
        ``flashloan_asset_usd_price`` – USD price per token (float)

    Call :func:`_validate_calldata_context` before this function to surface
    missing-key errors with actionable guidance.
    """
    if "optimal_input_base_units" in context:
        return _require_int_base_units(context, "optimal_input_base_units")
    token_meta = TokenUnitSpec(
        symbol=context["flashloan_asset_symbol"],
        decimals=int(context["flashloan_asset_decimals"]),
        usd_price=Decimal(str(context["flashloan_asset_usd_price"])),
    )
    return _usd_to_token_base_units(context["optimal_input"], token_meta)


def resolve_min_final_output_units(context: Mapping[str, Any]) -> int:
    """Resolve the minimum acceptable output in profit-token base units.

    Prefers ``min_final_output_base_units``.  Falls back to a USD → base-unit
    conversion using ``profit_token_*`` metadata keys.

    Required context keys (explicit path):
        ``min_final_output_base_units`` – integer base units

    Required context keys (USD fallback path):
        ``final_output``              – USD amount (float)
        ``profit_token_symbol``       – token symbol string
        ``profit_token_decimals``     – token decimals (int)
        ``profit_token_usd_price``    – USD price per token (float)

    Call :func:`_validate_calldata_context` before this function to surface
    missing-key errors with actionable guidance.
    """
    if "min_final_output_base_units" in context:
        return _require_int_base_units(context, "min_final_output_base_units")
    token_meta = TokenUnitSpec(
        symbol=context["profit_token_symbol"],
        decimals=int(context["profit_token_decimals"]),
        usd_price=Decimal(str(context["profit_token_usd_price"])),
    )
    return _usd_to_token_base_units(context["final_output"], token_meta)


def usd_to_native_wei(amount_usd: Decimal | float | int | str, chain_id: int) -> int:
    """Convert a USD profit amount to Wei of the chain's native token.

    Uses ``_NATIVE_USD_BY_CHAIN`` to look up the native-token USD price for
    *chain_id*.  Polygon (chain 137) maps to POL; Ethereum (chain 1) maps to
    ETH.  Raises ``ValueError`` for unsupported or zero-priced chains.

    Example: ``usd_to_native_wei(10, 137)`` → ``~11.76 × 10**18`` Wei POL
    (at 0.85 USD/POL).
    """
    native_usd = _NATIVE_USD_BY_CHAIN.get(chain_id)
    if native_usd is None or native_usd <= 0:
        raise ValueError(
            f"Unsupported or invalid native USD price for chain_id={chain_id}"
        )
    native_amount = Decimal(str(amount_usd)) / native_usd
    wei_amount = native_amount * Decimal(10 ** 18)
    return int(wei_amount.quantize(Decimal("1"), rounding=ROUND_DOWN))


def attach_flashloan_token_meta(
    sentinel_output: dict,
    flashloan_token: TokenUnitSpec,
    profit_token: Optional[TokenUnitSpec] = None,
) -> dict:
    """Inject base-unit fields into a :class:`~.slippage_sentinel.SlippageSentinel` output dict.

    ``SlippageSentinel.optimize()`` emits ``optimal_input`` and
    ``final_output`` as USD floats.  This helper converts those floats to
    integer token base units and injects the results under the keys
    ``optimal_input_base_units`` and ``min_final_output_base_units``, which
    are the preferred resolution path for
    :func:`resolve_optimal_input_units` /
    :func:`resolve_min_final_output_units`.

    Calling this function bridges the sentinel output directly to calldata
    without the caller having to manually attach token metadata.

    Parameters
    ----------
    sentinel_output:
        Dict returned by :meth:`~.slippage_sentinel.SlippageSentinel.optimize`
        or the ``build_c1_slippage_context`` / ``build_c2_slippage_context``
        variants.  The dict is mutated **in-place** and also returned for
        convenience.
    flashloan_token:
        Token spec for the flash-loan asset (the token being lent and
        repaid).  Provides ``decimals`` and ``usd_price`` for the
        ``optimal_input`` → base-unit conversion.
    profit_token:
        Token spec for the output/profit token.  When ``None`` (default)
        the flash-loan token is assumed to also be the profit token — the
        typical USDC round-trip pattern.

    Returns
    -------
    dict
        The same ``sentinel_output`` dict, now containing
        ``optimal_input_base_units`` and ``min_final_output_base_units``.

    Raises
    ------
    ValueError
        If either token spec lacks a valid ``usd_price``.
    KeyError
        If ``sentinel_output`` is missing the ``optimal_input`` or
        ``final_output`` keys.
    """
    profit_spec = profit_token or flashloan_token
    sentinel_output["optimal_input_base_units"] = _usd_to_token_base_units(
        sentinel_output["optimal_input"], flashloan_token
    )
    sentinel_output["min_final_output_base_units"] = _usd_to_token_base_units(
        sentinel_output["final_output"], profit_spec
    )
    return sentinel_output


class ContractInvoker:
    """Encode calldata and invoke target contracts via eth_call and optional signed tx.

    Supports both legacy (``gasPrice``) and EIP-1559 (``maxFeePerGas`` /
    ``maxPriorityFeePerGas``) transaction modes.  EIP-1559 mode is used when
    ``APEX_EIP1559`` is set to ``"1"`` in the environment (recommended for all
    EVM chains that support it, including Polygon).

    MEV bundle submission is available via :meth:`invoke_bundle` when
    ``APEX_MEV_RELAY_URL`` is configured.
    """

    def __init__(self, target_address: str, rpc_url: Optional[str] = None):
        self.target_address = Web3.to_checksum_address(target_address)
        self.rpc_url = rpc_url or os.getenv("APEX_RPC_URL", "https://polygon-rpc.com/")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.private_key = os.getenv("APEX_PRIVATE_KEY")
        self.send_tx = os.getenv("APEX_SEND_TX", "0") == "1"
        self.wait_receipt = os.getenv("APEX_WAIT_RECEIPT", "1") == "1"
        self.tx_timeout = int(os.getenv("APEX_TX_TIMEOUT", "90"))
        self.use_eip1559 = os.getenv("APEX_EIP1559", "1") == "1"
        self.account = self.w3.eth.account.from_key(self.private_key) if self.private_key else None
        self._gas_oracle = GasOracle(rpc_url=self.rpc_url)
        self._state_store = get_execution_state_store()
        self._telegram: EventNotifierBase = build_notifier()

    def _selector(self, signature: str) -> bytes:
        return Web3.keccak(text=signature)[:4]

    def _encode_call(self, signature: str, arg_types: list[str], args: list[Any]) -> str:
        selector = self._selector(signature)
        encoded_args = encode(arg_types, args)
        return Web3.to_hex(selector + encoded_args)

    def build_c1_calldata(self, strike_plan: Dict[str, Any]) -> str:
        """Build calldata for C1 strike contract.

        Amounts are resolved to token-native integer base units via
        :func:`resolve_optimal_input_units` and
        :func:`resolve_min_final_output_units`.  If the sentinel output
        contains pre-computed base-unit fields (``optimal_input_base_units``
        / ``min_final_output_base_units``) those are used directly; otherwise
        the helpers perform a USD → base-unit conversion using the token
        metadata embedded in the context.

        Raises :exc:`ValueError` with actionable guidance when the context
        lacks both the direct base-unit keys and the required token metadata
        for the USD fallback path.  ``SlippageSentinel`` outputs only USD
        floats; callers must attach either the pre-computed base-unit keys or
        the ``flashloan_asset_*`` / ``profit_token_*`` metadata before
        building calldata.
        """
        context = strike_plan["sentinel_output"]
        _validate_calldata_context(context)
        asset_in_units = resolve_optimal_input_units(context)
        min_final_out_units = resolve_min_final_output_units(context)
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call(
            "strike(uint256,uint256,int256)",
            ["uint256", "uint256", "int256"],
            [asset_in_units, min_final_out_units, raw_spread],
        )

    def build_c2_calldata(self, decision_plan: Dict[str, Any]) -> str:
        """Build calldata for C2 decision/strike contract.

        See :meth:`build_c1_calldata` for amount resolution semantics and
        context key requirements.

        Raises :exc:`ValueError` with actionable guidance when the context
        lacks the required base-unit or token-metadata keys.
        """
        context = decision_plan["sentinel_output"]
        _validate_calldata_context(context)
        decision = str(decision_plan.get("decision", "DO_NOTHING"))
        decision_code = {
            "DO_NOTHING": 0,
            "STRIKE": 1,
            "DUPLICATE": 2,
            "REVERSE": 3,
        }.get(decision, 0)
        asset_in_units = resolve_optimal_input_units(context)
        min_final_out_units = resolve_min_final_output_units(context)
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call(
            "decide(uint8,uint256,uint256,int256)",
            ["uint8", "uint256", "uint256", "int256"],
            [decision_code, asset_in_units, min_final_out_units, raw_spread],
        )

    def _eth_call(self, calldata: str) -> Dict[str, Any]:
        call_tx = {
            "to": self.target_address,
            "data": calldata,
        }
        try:
            output = self.w3.eth.call(call_tx)
            return {
                "ok": True,
                "output": Web3.to_hex(output),
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "output": None,
                "error": str(exc),
            }

    def _event_base(
        self,
        *,
        chain_id: int,
        gas_limit: int | None,
        gas_price_wei: int | None,
        context: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        ctx = dict(context or {})
        idempotency_key = str(ctx.get("idempotency_key") or Web3.keccak(text=f"{chain_id}:{time.time_ns()}").hex())
        return {
            "opportunity_id": ctx.get("opportunity_id"),
            "idempotency_key": idempotency_key,
            "chain_id": chain_id,
            "chain_name": chain_name_for(chain_id),
            "executor_contract": self.target_address,
            "wallet_address": ctx.get("wallet_address") or (self.account.address if self.account else None),
            "token_pair": ctx.get("token_pair") or "unknown",
            "loan_amount_usd": ctx.get("loan_amount_usd"),
            "expected_profit_usd": ctx.get("expected_profit_usd"),
            "min_profit": ctx.get("min_profit"),
            "gas_price_gwei": (float(gas_price_wei) / 1_000_000_000.0) if gas_price_wei else None,
            "gas_limit": int(gas_limit) if gas_limit else None,
            "block_number": None,
            "gas_used": None,
            "tx_hash": None,
            "explorer_url": None,
            "rejection_reasons": [],
            "timestamp": time.time(),
        }

    def _record_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        stored = self._state_store.append(event)
        # Dispatch notification via the bounded pool — never blocks the trading path.
        _NOTIFY_POOL.submit(self._telegram.send_event, stored)
        return stored

    def invoke(
        self,
        calldata: str,
        p_net_usd: float = 0.0,
        execution_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Always simulate via eth_call; optionally broadcast a signed transaction.

        When ``APEX_EIP1559=1`` (the default) the transaction uses EIP-1559
        dynamic fees derived from the :class:`~.mev_gas_oracle.TipOptimizer`.
        Pass ``p_net_usd`` so the optimizer can select the correct tip for the
        ``P_net × P(fill) > 0`` guardrail.
        """
        simulation = self._eth_call(calldata)
        result: Dict[str, Any] = {
            "target": self.target_address,
            "calldata": calldata,
            "simulation": simulation,
            "broadcast": None,
            "success": False,
            "executed_onchain": False,
            "simulation_only": False,
            "tx_hash": None,
        }
        if self.w3.is_connected():
            chain_id = int(self.w3.eth.chain_id)
        else:
            ctx_chain_id = execution_context.get("chain_id", 0) if execution_context else 0
            chain_id = int(ctx_chain_id or 0)
        base_event = self._event_base(
            chain_id=chain_id,
            gas_limit=None,
            gas_price_wei=None,
            context={**dict(execution_context or {}), "expected_profit_usd": p_net_usd},
        )

        if not simulation["ok"]:
            self._record_event(
                {
                    **base_event,
                    "status": "rejected",
                    "rejection_reasons": [simulation.get("error") or "simulation_failed"],
                }
            )
            return result

        if not self.send_tx:
            result["simulation_only"] = True
            result["broadcast"] = {"status": "not_sent", "reason": "APEX_SEND_TX != 1"}
            result["success"] = True
            self._record_event(
                {
                    **base_event,
                    "status": "dry_run",
                    "rejection_reasons": ["APEX_SEND_TX != 1"],
                }
            )
            return result

        if self.account is None:
            result["broadcast"] = {"error": "APEX_PRIVATE_KEY not set"}
            self._record_event(
                {
                    **base_event,
                    "status": "rejected",
                    "rejection_reasons": ["APEX_PRIVATE_KEY not set"],
                }
            )
            return result

        from_address = self.account.address
        nonce = self.w3.eth.get_transaction_count(from_address)
        chain_id = self.w3.eth.chain_id
        gas_estimate = self.w3.eth.estimate_gas({
            "from": from_address,
            "to": self.target_address,
            "data": calldata,
            "value": 0,
        })

        if self.use_eip1559:
            tx = self._build_eip1559_tx(
                nonce=nonce,
                chain_id=chain_id,
                calldata=calldata,
                gas_estimate=gas_estimate,
                p_net_usd=p_net_usd,
            )
        else:
            gas_price = self.w3.eth.gas_price
            tx = {
                "chainId": chain_id,
                "nonce": nonce,
                "to": self.target_address,
                "value": 0,
                "data": calldata,
                "gas": int(gas_estimate * 1.2),
                "gasPrice": gas_price,
            }

        try:
            signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
            tx_hash_bytes = self.w3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hash = Web3.to_hex(tx_hash_bytes)
            result["tx_hash"] = tx_hash
        except Exception as exc:
            result["broadcast"] = {"error": str(exc)}
            self._record_event(
                {
                    **base_event,
                    "status": "rejected",
                    "gas_limit": int(tx.get("gas", 0)),
                    "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0,
                    "rejection_reasons": [str(exc)],
                }
            )
            return result

        submitted_event = self._record_event(
            {
                **base_event,
                "status": "submitted",
                "tx_hash": tx_hash,
                "explorer_url": explorer_url_for(chain_id, tx_hash),
                "gas_limit": int(tx.get("gas", 0)),
                "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0,
            }
        )
        result["explorer_url"] = submitted_event.get("explorer_url")

        if self.wait_receipt:
            try:
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=self.tx_timeout)
                result["broadcast"] = {
                    "status": int(receipt.status),
                    "blockNumber": receipt.blockNumber,
                    "gasUsed": int(receipt.gasUsed),
                }
                result["success"] = int(receipt.status) == 1
                result["executed_onchain"] = int(receipt.status) == 1
                self._record_event(
                    {
                        **base_event,
                        "status": "confirmed" if int(receipt.status) == 1 else "reverted",
                        "tx_hash": tx_hash,
                        "explorer_url": explorer_url_for(chain_id, tx_hash),
                        "block_number": int(receipt.blockNumber),
                        "gas_used": int(receipt.gasUsed),
                        "gas_limit": int(tx.get("gas", 0)),
                        "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0,
                        "rejection_reasons": [] if int(receipt.status) == 1 else ["transaction reverted"],
                    }
                )
            except Exception as exc:
                result["broadcast"] = {"status": "submitted", "receipt_error": str(exc)}
                result["success"] = False
                self._record_event(
                    {
                        **base_event,
                        "status": "submitted",
                        "tx_hash": tx_hash,
                        "explorer_url": explorer_url_for(chain_id, tx_hash),
                        "gas_limit": int(tx.get("gas", 0)),
                        "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0,
                        "receipt_error": str(exc),
                        "rejection_reasons": [str(exc)],
                    }
                )
        else:
            result["broadcast"] = {"status": "submitted"}
            result["success"] = True
            result["executed_onchain"] = True

        return result

    async def invoke_bundle(
        self,
        calldata: str,
        p_net_usd: float = 0.0,
        gas_units: int = 350_000,
        simulate_only: bool = False,
    ) -> Dict[str, Any]:
        """Build, optionally simulate, and submit an MEV bundle.

        Parameters
        ----------
        calldata      : ABI-encoded call for the target contract
        p_net_usd     : expected net profit in USD (drives tip optimisation)
        gas_units     : estimated gas consumption
        simulate_only : when True, run ``eth_callBundle`` but do not submit

        Returns
        -------
        dict with keys ``success``, ``tx_hash``, ``simulation``, ``submission``,
        ``eip1559_params``, and ``bundle_hash``.
        """
        from .mev_bundle import BundleBuilder, BundleSimulator, BundleSubmitter

        snapshot = self._gas_oracle.get_snapshot()
        optimizer = TipOptimizer(snapshot, gas_units=gas_units)
        eip1559 = optimizer.build_eip1559_params(p_net_usd)

        builder = BundleBuilder(w3=self.w3, private_key=self.private_key)
        # Resolve chain ID from the live node so the profit threshold is
        # denominated in the correct chain-native token (POL on Polygon,
        # ETH on Ethereum).  Failure is a hard error: a wrong chain_id would
        # silently mis-denominate min_profit_wei and risk incorrect bundle
        # acceptance on the target network.
        try:
            chain_id = int(self.w3.eth.chain_id)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot fetch chain_id from RPC before bundle submission: {exc}. "
                "Ensure the Web3 provider is reachable and correctly configured."
            ) from exc
        bundle = builder.assemble(
            calldata=calldata,
            target_address=self.target_address,
            gas=int(gas_units * 1.2),
            max_fee_per_gas=eip1559["maxFeePerGas"],
            max_priority_fee_per_gas=eip1559["maxPriorityFeePerGas"],
            min_profit_wei=usd_to_native_wei(max(0.0, p_net_usd), chain_id),
        )

        result: Dict[str, Any] = {
            "success": False,
            "tx_hash": None,
            "simulation": None,
            "submission": None,
            "eip1559_params": eip1559,
            "bundle_hash": "",
        }

        if bundle is None:
            result["error"] = "Bundle assembly failed (missing private key?)"
            return result

        sim_result = await BundleSimulator().simulate(bundle)
        result["simulation"] = sim_result

        if not sim_result["success"]:
            result["error"] = sim_result.get("error", "Simulation failed")
            return result

        if simulate_only:
            result["success"] = True
            return result

        sub_result = await BundleSubmitter().submit(bundle)
        result["submission"] = sub_result
        result["bundle_hash"] = sub_result.get("bundle_hash", "")
        result["success"] = sub_result.get("success", False)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_eip1559_tx(
        self,
        nonce: int,
        chain_id: int,
        calldata: str,
        gas_estimate: int,
        p_net_usd: float,
    ) -> Dict[str, Any]:
        """Build an EIP-1559 transaction dict with tip-optimised gas params."""
        snapshot = self._gas_oracle.get_snapshot()
        optimizer = TipOptimizer(snapshot, gas_units=gas_estimate)
        eip1559 = optimizer.build_eip1559_params(p_net_usd)
        return {
            "type": 2,
            "chainId": chain_id,
            "nonce": nonce,
            "to": self.target_address,
            "value": 0,
            "data": calldata,
            "gas": int(gas_estimate * 1.2),
            "maxFeePerGas": eip1559["maxFeePerGas"],
            "maxPriorityFeePerGas": eip1559["maxPriorityFeePerGas"],
        }
