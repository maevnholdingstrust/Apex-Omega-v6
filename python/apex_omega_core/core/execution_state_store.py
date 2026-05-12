from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

CHAIN_NAMES: dict[int, str] = {
    1: "Ethereum",
    10: "Optimism",
    56: "BNB",
    137: "Polygon",
    42161: "Arbitrum",
    43114: "Avalanche",
    8453: "Base",
    250: "Fantom",
    324: "zkSync Era",
    59144: "Linea",
}

CHAIN_EXPLORERS: dict[int, str] = {
    1: "https://etherscan.io/tx/{tx_hash}",
    10: "https://optimistic.etherscan.io/tx/{tx_hash}",
    56: "https://bscscan.com/tx/{tx_hash}",
    137: "https://polygonscan.com/tx/{tx_hash}",
    42161: "https://arbiscan.io/tx/{tx_hash}",
    43114: "https://snowtrace.io/tx/{tx_hash}",
    8453: "https://basescan.org/tx/{tx_hash}",
    250: "https://ftmscan.com/tx/{tx_hash}",
    324: "https://era.zksync.network/tx/{tx_hash}",
    59144: "https://lineascan.build/tx/{tx_hash}",
}

REQUIRED_FIELDS: tuple[str, ...] = (
    "opportunity_id",
    "idempotency_key",
    "chain_id",
    "chain_name",
    "executor_contract",
    "wallet_address",
    "token_pair",
    "loan_amount_usd",
    "expected_profit_usd",
    "min_profit",
    "gas_price_gwei",
    "gas_limit",
    "status",
    "tx_hash",
    "explorer_url",
    "block_number",
    "gas_used",
    "rejection_reasons",
    "timestamp",
)


def explorer_url_for(chain_id: int | None, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    template = CHAIN_EXPLORERS.get(int(chain_id or 0))
    if template is None:
        return None
    return template.format(tx_hash=tx_hash)


def chain_name_for(chain_id: int | None) -> str:
    return CHAIN_NAMES.get(int(chain_id or 0), f"Chain-{int(chain_id or 0)}")


def _default_store_path() -> Path:
    configured = os.getenv("APEX_EXECUTION_STATE_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "logs" / "execution_state_history.jsonl"


class ExecutionStateStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _normalize(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized = {key: event.get(key) for key in REQUIRED_FIELDS}
        normalized["chain_id"] = int(normalized.get("chain_id") or 0)
        normalized["chain_name"] = normalized.get("chain_name") or chain_name_for(normalized["chain_id"])
        normalized["status"] = str(normalized.get("status") or "rejected")
        normalized["timestamp"] = float(normalized.get("timestamp") or time.time())
        tx_hash = normalized.get("tx_hash")
        normalized["tx_hash"] = tx_hash
        normalized["explorer_url"] = normalized.get("explorer_url") or explorer_url_for(
            normalized.get("chain_id"), tx_hash
        )
        reasons = normalized.get("rejection_reasons")
        if reasons is None:
            normalized["rejection_reasons"] = []
        elif isinstance(reasons, str):
            normalized["rejection_reasons"] = [reasons]
        else:
            normalized["rejection_reasons"] = list(reasons)
        return normalized

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        return normalized

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        tail = deque(maxlen=max(1, int(limit)))
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    tail.append(line)
        records: list[dict[str, Any]] = []
        for line in reversed(list(tail)):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


_STORE: ExecutionStateStore | None = None


def get_execution_state_store() -> ExecutionStateStore:
    global _STORE
    if _STORE is None:
        _STORE = ExecutionStateStore()
    return _STORE
