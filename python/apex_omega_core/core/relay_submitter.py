from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

from .runtime_config import RuntimeConfig


@dataclass(frozen=True)
class BundleSubmissionResult:
    relay: str
    url: str
    status: str
    latency_ms: float
    response: Any | None = None
    error: str | None = None


class RelayBundleSubmitter:
    """MEV relay submitter for raw signed transaction bundles.

    rpc_tester.py proves endpoint reachability. This module actually formats and
    sends bundle JSON-RPC payloads. It remains guarded by RuntimeConfig so dry
    runs cannot accidentally submit transactions.
    """

    def __init__(self, config: RuntimeConfig, timeout: float = 5.0):
        self.config = config
        self.timeout = timeout

    def _post_json(self, url: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"http_status": resp.status}

    @staticmethod
    def build_eth_send_bundle_payload(raw_txs: Iterable[str], target_block: int) -> dict[str, Any]:
        txs = [tx if tx.startswith("0x") else f"0x{tx}" for tx in raw_txs]
        if not txs:
            raise ValueError("bundle requires at least one raw signed transaction")
        if target_block <= 0:
            raise ValueError("target_block must be positive")
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [{"txs": txs, "blockNumber": hex(target_block)}],
        }

    def submit_bundle(self, raw_txs: Iterable[str], target_block: int) -> list[BundleSubmissionResult]:
        self.config.assert_safe_to_send()
        payload = self.build_eth_send_bundle_payload(raw_txs, target_block)
        results: list[BundleSubmissionResult] = []
        for relay, url in self.config.relays.items():
            started = time.perf_counter()
            try:
                response = self._post_json(url, payload)
                results.append(BundleSubmissionResult(relay, url, "submitted", (time.perf_counter() - started) * 1000, response, None))
            except Exception as exc:  # noqa: BLE001
                results.append(BundleSubmissionResult(relay, url, "error", (time.perf_counter() - started) * 1000, None, str(exc)))
        if not results:
            raise RuntimeError("No MEV relay URLs configured")
        return results

    def dry_run_payload(self, raw_txs: Iterable[str], target_block: int) -> dict[str, Any]:
        return self.build_eth_send_bundle_payload(raw_txs, target_block)
