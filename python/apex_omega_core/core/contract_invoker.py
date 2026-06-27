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
_NOTIFY_POOL: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apex-notify")

_NATIVE_USD_BY_CHAIN: Dict[int, Decimal] = {
    1: Decimal(os.getenv("APEX_ETH_USD", "3500")),
    137: Decimal(os.getenv("APEX_POL_USD", "0.85")),
}


@dataclass(frozen=True)
class TokenUnitSpec:
    symbol: str
    decimals: int
    usd_price: Optional[Decimal] = None


def _to_base_units(amount_tokens: Decimal | float | int | str, decimals: int) -> int:
    q = Decimal(str(amount_tokens)) * (Decimal(10) ** decimals)
    return int(q.quantize(Decimal("1"), rounding=ROUND_DOWN))


def _usd_to_token_base_units(amount_usd: Decimal | float | int | str, token: TokenUnitSpec) -> int:
    if token.usd_price is None or token.usd_price <= 0:
        raise ValueError(f"Missing or invalid usd_price for token {token.symbol!r}")
    token_amount = Decimal(str(amount_usd)) / token.usd_price
    return _to_base_units(token_amount, token.decimals)


def _require_int_base_units(context: Mapping[str, Any], key: str) -> int:
    value = context.get(key)
    if value is None:
        raise KeyError(f"Missing required base-unit field: {key!r}")
    ivalue = int(value)
    if ivalue < 0:
        raise ValueError(f"Negative base-unit value for {key!r}: {ivalue}")
    return ivalue


_OPTIMAL_INPUT_DIRECT_KEY = "optimal_input_base_units"
_OPTIMAL_INPUT_USD_KEYS = frozenset({"optimal_input", "flashloan_asset_symbol", "flashloan_asset_decimals", "flashloan_asset_usd_price"})
_FINAL_OUTPUT_DIRECT_KEY = "min_final_output_base_units"
_FINAL_OUTPUT_USD_KEYS = frozenset({"final_output", "profit_token_symbol", "profit_token_decimals", "profit_token_usd_price"})


def _validate_calldata_context(context: Mapping[str, Any]) -> None:
    errors: list[str] = []
    has_input_direct = _OPTIMAL_INPUT_DIRECT_KEY in context
    missing_input_usd = _OPTIMAL_INPUT_USD_KEYS - context.keys()
    if not has_input_direct and missing_input_usd:
        errors.append(f"Cannot resolve optimal_input to base units. Missing keys: {sorted(missing_input_usd)}.")
    has_output_direct = _FINAL_OUTPUT_DIRECT_KEY in context
    missing_output_usd = _FINAL_OUTPUT_USD_KEYS - context.keys()
    if not has_output_direct and missing_output_usd:
        errors.append(f"Cannot resolve min_final_output to base units. Missing keys: {sorted(missing_output_usd)}.")
    if errors:
        raise ValueError("Calldata context is missing required fields. " + " | ".join(errors))


def resolve_optimal_input_units(context: Mapping[str, Any]) -> int:
    if "optimal_input_base_units" in context:
        return _require_int_base_units(context, "optimal_input_base_units")
    token_meta = TokenUnitSpec(
        symbol=context["flashloan_asset_symbol"],
        decimals=int(context["flashloan_asset_decimals"]),
        usd_price=Decimal(str(context["flashloan_asset_usd_price"])),
    )
    return _usd_to_token_base_units(context["optimal_input"], token_meta)


def resolve_min_final_output_units(context: Mapping[str, Any]) -> int:
    if "min_final_output_base_units" in context:
        return _require_int_base_units(context, "min_final_output_base_units")
    token_meta = TokenUnitSpec(
        symbol=context["profit_token_symbol"],
        decimals=int(context["profit_token_decimals"]),
        usd_price=Decimal(str(context["profit_token_usd_price"])),
    )
    return _usd_to_token_base_units(context["final_output"], token_meta)


def usd_to_native_wei(amount_usd: Decimal | float | int | str, chain_id: int) -> int:
    native_usd = _NATIVE_USD_BY_CHAIN.get(chain_id)
    if native_usd is None or native_usd <= 0:
        raise ValueError(f"Unsupported or invalid native USD price for chain_id={chain_id}")
    native_amount = Decimal(str(amount_usd)) / native_usd
    wei_amount = native_amount * Decimal(10 ** 18)
    return int(wei_amount.quantize(Decimal("1"), rounding=ROUND_DOWN))


def attach_flashloan_token_meta(sentinel_output: dict, flashloan_token: TokenUnitSpec, profit_token: Optional[TokenUnitSpec] = None) -> dict:
    profit_spec = profit_token or flashloan_token
    sentinel_output["optimal_input_base_units"] = _usd_to_token_base_units(sentinel_output["optimal_input"], flashloan_token)
    sentinel_output["min_final_output_base_units"] = _usd_to_token_base_units(sentinel_output["final_output"], profit_spec)
    return sentinel_output


class ContractInvoker:
    """Encode calldata and invoke target contracts through eth_call and optional signed tx."""

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
        payload = strike_plan.get("vm_payload") or strike_plan.get("payload")
        if payload is not None:
            from .c1_payload_gate import C1GateConfig, C1PayloadGate
            from .execution_vm_calldata import build_c1_vm_calldata
            record = strike_plan.get("lock_record") or strike_plan.get("record")
            current_block = strike_plan.get("current_block")
            built = build_c1_vm_calldata(payload)
            gate = C1PayloadGate(
                C1GateConfig(
                    min_net_profit_usd=float(os.getenv("MIN_NET_PROFIT_USD", "5")),
                    current_block=int(current_block) if current_block is not None else None,
                    expected_target_contract=self.target_address,
                )
            )
            checked = gate.validate(payload, record, built_calldata_hash=built.calldataHash)
            checked.raise_if_failed()
            return built.calldata

        context = strike_plan["sentinel_output"]
        _validate_calldata_context(context)
        asset_in_units = resolve_optimal_input_units(context)
        min_final_out_units = resolve_min_final_output_units(context)
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call("strike(uint256,uint256,int256)", ["uint256", "uint256", "int256"], [asset_in_units, min_final_out_units, raw_spread])

    def build_c2_calldata(self, decision_plan: Dict[str, Any]) -> str:
        required = ("c1_block", "current_block")
        if any(k in decision_plan for k in required):
            c1_block = int(decision_plan.get("c1_block", 0))
            current_block = int(decision_plan.get("current_block", 0))
            max_delay = int(decision_plan.get("max_delay_blocks", 5))
            if current_block <= c1_block:
                raise ValueError("C2 requires current_block after c1_block")
            if current_block > c1_block + max_delay:
                raise ValueError("C2 execution window expired")

        context = decision_plan["sentinel_output"]
        _validate_calldata_context(context)
        decision = str(decision_plan.get("decision", "DO_NOTHING"))
        decision_code = {"DO_NOTHING": 0, "STRIKE": 1, "DUPLICATE": 2, "REVERSE": 3, "MIRROR": 2}.get(decision, 0)
        asset_in_units = resolve_optimal_input_units(context)
        min_final_out_units = resolve_min_final_output_units(context)
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call("decide(uint8,uint256,uint256,int256)", ["uint8", "uint256", "uint256", "int256"], [decision_code, asset_in_units, min_final_out_units, raw_spread])

    def _eth_call(self, calldata: str) -> Dict[str, Any]:
        call_tx = {"to": self.target_address, "data": calldata}
        try:
            output = self.w3.eth.call(call_tx)
            return {"ok": True, "output": Web3.to_hex(output), "error": None}
        except Exception as exc:
            return {"ok": False, "output": None, "error": str(exc)}

    def _event_base(self, *, chain_id: int, gas_limit: int | None, gas_price_wei: int | None, context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
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
        _NOTIFY_POOL.submit(self._telegram.send_event, stored)
        return stored

    def invoke(self, calldata: str, p_net_usd: float = 0.0, execution_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        simulation = self._eth_call(calldata)
        result: Dict[str, Any] = {"target": self.target_address, "calldata": calldata, "simulation": simulation, "broadcast": None, "success": False, "executed_onchain": False, "simulation_only": False, "tx_hash": None}
        if self.w3.is_connected():
            chain_id = int(self.w3.eth.chain_id)
        else:
            ctx_chain_id = execution_context.get("chain_id", 0) if execution_context else 0
            chain_id = int(ctx_chain_id or 0)
        base_event = self._event_base(chain_id=chain_id, gas_limit=None, gas_price_wei=None, context={**dict(execution_context or {}), "expected_profit_usd": p_net_usd})

        if not simulation["ok"]:
            self._record_event({**base_event, "status": "rejected", "rejection_reasons": [simulation.get("error") or "simulation_failed"]})
            return result

        if not self.send_tx:
            result["simulation_only"] = True
            result["broadcast"] = {"status": "not_sent", "reason": "APEX_SEND_TX != 1"}
            result["success"] = True
            self._record_event({**base_event, "status": "dry_run", "rejection_reasons": ["APEX_SEND_TX != 1"]})
            return result

        if self.account is None:
            result["broadcast"] = {"error": "APEX_PRIVATE_KEY not set"}
            self._record_event({**base_event, "status": "rejected", "rejection_reasons": ["APEX_PRIVATE_KEY not set"]})
            return result

        from_address = self.account.address
        nonce = self.w3.eth.get_transaction_count(from_address)
        chain_id = self.w3.eth.chain_id
        gas_estimate = self.w3.eth.estimate_gas({"from": from_address, "to": self.target_address, "data": calldata, "value": 0})

        if self.use_eip1559:
            tx = self._build_eip1559_tx(nonce=nonce, chain_id=chain_id, calldata=calldata, gas_estimate=gas_estimate, p_net_usd=p_net_usd)
        else:
            gas_price = self.w3.eth.gas_price
            tx = {"chainId": chain_id, "nonce": nonce, "to": self.target_address, "value": 0, "data": calldata, "gas": int(gas_estimate * 1.2), "gasPrice": gas_price}

        try:
            signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
            tx_hash_bytes = self.w3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hash = Web3.to_hex(tx_hash_bytes)
            result["tx_hash"] = tx_hash
        except Exception as exc:
            result["broadcast"] = {"error": str(exc)}
            self._record_event({**base_event, "status": "rejected", "gas_limit": int(tx.get("gas", 0)), "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0, "rejection_reasons": [str(exc)]})
            return result

        submitted_event = self._record_event({**base_event, "status": "submitted", "tx_hash": tx_hash, "explorer_url": explorer_url_for(chain_id, tx_hash), "gas_limit": int(tx.get("gas", 0)), "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0})
        result["explorer_url"] = submitted_event.get("explorer_url")

        if self.wait_receipt:
            try:
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=self.tx_timeout)
                result["broadcast"] = {"status": int(receipt.status), "blockNumber": receipt.blockNumber, "gasUsed": int(receipt.gasUsed)}
                result["success"] = int(receipt.status) == 1
                result["executed_onchain"] = int(receipt.status) == 1
                self._record_event({**base_event, "status": "confirmed" if int(receipt.status) == 1 else "reverted", "tx_hash": tx_hash, "explorer_url": explorer_url_for(chain_id, tx_hash), "block_number": int(receipt.blockNumber), "gas_used": int(receipt.gasUsed), "gas_limit": int(tx.get("gas", 0)), "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0, "rejection_reasons": [] if int(receipt.status) == 1 else ["transaction reverted"]})
            except Exception as exc:
                result["broadcast"] = {"status": "submitted", "receipt_error": str(exc)}
                result["success"] = False
                self._record_event({**base_event, "status": "submitted", "tx_hash": tx_hash, "explorer_url": explorer_url_for(chain_id, tx_hash), "gas_limit": int(tx.get("gas", 0)), "gas_price_gwei": float(tx.get("gasPrice", tx.get("maxFeePerGas", 0)) or 0) / 1_000_000_000.0, "receipt_error": str(exc), "rejection_reasons": [str(exc)]})
        else:
            result["broadcast"] = {"status": "submitted"}
            result["success"] = True
            result["executed_onchain"] = False

        return result

    def _build_eip1559_tx(self, *, nonce: int, chain_id: int, calldata: str, gas_estimate: int, p_net_usd: float) -> Dict[str, Any]:
        snapshot = self._gas_oracle.get_snapshot()
        optimizer = TipOptimizer(snapshot, gas_units=int(gas_estimate * 1.2))
        params = optimizer.build_eip1559_params(p_net_usd)
        return {
            "chainId": chain_id,
            "nonce": nonce,
            "to": self.target_address,
            "value": 0,
            "data": calldata,
            "gas": int(gas_estimate * 1.2),
            "maxFeePerGas": int(params.get("max_fee_per_gas_wei", self.w3.eth.gas_price)),
            "maxPriorityFeePerGas": int(params.get("max_priority_fee_per_gas_wei", self.w3.eth.max_priority_fee)),
        }

    def invoke_bundle(self, calldata: str, p_net_usd: float = 0.0, execution_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        relay_url = os.getenv("APEX_MEV_RELAY_URL")
        if not relay_url:
            return self.invoke(calldata, p_net_usd=p_net_usd, execution_context=execution_context)
        return self.invoke(calldata, p_net_usd=p_net_usd, execution_context=execution_context)
