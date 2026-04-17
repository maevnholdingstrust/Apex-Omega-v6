import os
from typing import Any, Dict, Optional

from eth_abi import encode
from web3 import Web3

from .mev_gas_oracle import GasOracle, TipOptimizer


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

    def _selector(self, signature: str) -> bytes:
        return Web3.keccak(text=signature)[:4]

    def _encode_call(self, signature: str, arg_types: list[str], args: list[Any]) -> str:
        selector = self._selector(signature)
        encoded_args = encode(arg_types, args)
        return Web3.to_hex(selector + encoded_args)

    def build_c1_calldata(self, strike_plan: Dict[str, Any]) -> str:
        """Build calldata for C1 strike contract."""
        context = strike_plan["sentinel_output"]
        optimal_input = int(max(0.0, float(context["optimal_input"])))
        final_output = int(max(0.0, float(context["final_output"])))
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call(
            "strike(uint256,uint256,int256)",
            ["uint256", "uint256", "int256"],
            [optimal_input, final_output, raw_spread],
        )

    def build_c2_calldata(self, decision_plan: Dict[str, Any]) -> str:
        """Build calldata for C2 decision/strike contract."""
        context = decision_plan["sentinel_output"]
        decision = str(decision_plan.get("decision", "DO_NOTHING"))
        decision_code = {
            "DO_NOTHING": 0,
            "STRIKE": 1,
            "DUPLICATE": 2,
            "REVERSE": 3,
        }.get(decision, 0)
        optimal_input = int(max(0.0, float(context["optimal_input"])))
        final_output = int(max(0.0, float(context["final_output"])))
        raw_spread = int(float(context.get("raw_spread", 0.0)) * 1_000_000)
        return self._encode_call(
            "decide(uint8,uint256,uint256,int256)",
            ["uint8", "uint256", "uint256", "int256"],
            [decision_code, optimal_input, final_output, raw_spread],
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

    def invoke(self, calldata: str, p_net_usd: float = 0.0) -> Dict[str, Any]:
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
            "tx_hash": None,
        }

        if not simulation["ok"]:
            return result

        if not self.send_tx:
            result["success"] = True
            return result

        if self.account is None:
            result["broadcast"] = {"error": "APEX_PRIVATE_KEY not set"}
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

        signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
        tx_hash_bytes = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hash = Web3.to_hex(tx_hash_bytes)
        result["tx_hash"] = tx_hash

        if self.wait_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=self.tx_timeout)
            result["broadcast"] = {
                "status": int(receipt.status),
                "blockNumber": receipt.blockNumber,
                "gasUsed": int(receipt.gasUsed),
            }
            result["success"] = int(receipt.status) == 1
        else:
            result["broadcast"] = {"status": "submitted"}
            result["success"] = True

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
        bundle = builder.assemble(
            calldata=calldata,
            target_address=self.target_address,
            gas=int(gas_units * 1.2),
            max_fee_per_gas=eip1559["maxFeePerGas"],
            max_priority_fee_per_gas=eip1559["maxPriorityFeePerGas"],
            min_profit_wei=int(max(0.0, p_net_usd) * 1e18 // 3500),
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