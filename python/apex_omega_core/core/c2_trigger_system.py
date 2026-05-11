from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from web3 import Web3

from .execution_engine import ExecutionEngine, ExecutionPlan


@dataclass(frozen=True)
class C2Decision:
    action: str  # "execute" | "idle"
    reason: str
    plan: ExecutionPlan | None = None


class C2TriggerSystem:
    """Observes C1 execution and conditionally triggers C2.

    Inject dependencies to keep this layer deterministic and testable:
    - `state_fetcher`: given a receipt, returns post-C1 pool/state snapshot
    - `strategy_builder`: given post-state, returns C2 strategy_output
    """

    def __init__(
        self,
        w3: Web3,
        engine: ExecutionEngine,
        state_fetcher: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        strategy_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ):
        self.w3 = w3
        self.engine = engine
        self.state_fetcher = state_fetcher
        self.strategy_builder = strategy_builder

    def await_c1_receipt(self, tx_hash: str, timeout_blocks: int = 5) -> Mapping[str, Any] | None:
        start_block = self.w3.eth.block_number
        for _ in range(timeout_blocks):
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt:
                    return dict(receipt)
            except Exception:
                pass
            self.w3.provider.make_request("eth_blockNumber", [])
        return None

    def decide_and_build(self, c1_tx_hash: str) -> C2Decision:
        receipt = self.await_c1_receipt(c1_tx_hash)
        if receipt is None:
            return C2Decision("idle", "C1 not confirmed in window", None)

        if receipt.get("status") != 1:
            return C2Decision("idle", "C1 reverted", None)

        post_state = self.state_fetcher(receipt)
        if not post_state:
            return C2Decision("idle", "post-state unavailable", None)

        strategy_output = self.strategy_builder(post_state)
        if not strategy_output:
            return C2Decision("idle", "no residual opportunity", None)

        opportunity = strategy_output.get("opportunity", {})
        try:
            self.engine.validate_opportunity(opportunity)
        except Exception as exc:  # noqa: BLE001
            return C2Decision("idle", f"validation failed: {exc}", None)

        plan = self.engine.build_c2_plan(strategy_output)
        return C2Decision("execute", "residual EV positive", plan)
