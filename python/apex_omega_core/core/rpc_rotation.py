"""Robust Polygon JSON-RPC rotation and health scoring.

This module is read-side infrastructure. It rejects wrong-chain endpoints,
measures latency and block freshness, cools down failing nodes, and keeps
execution/scanner code from depending on one static RPC URL.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .rpc_discovery import discover_public_rpc_urls


POLYGON_CHAIN_ID = 137
DEFAULT_POLYGON_RPC_URLS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
    "https://polygon-rpc.com/",
)
RPC_ENV_KEYS = (
    "ACTIVE_EXECUTION_RPC",
    "ACTIVE_DISCOVERY_RPC",
    "POLYGON_RPC_URL",
    "POLYGON_HTTP",
    "POLYGON_RPC",
    "PRIVATE_RPC_URL",
    "ALCHEMY_HTTP_1",
    "ALCHEMY_HTTP_2",
    "INFURA_HTTP",
    "PUBLIC_DRPC",
)


class RpcRotationError(RuntimeError):
    """Raised when no usable RPC endpoint remains."""


@dataclass
class RpcEndpointState:
    label: str
    url: str
    status: str = "unchecked"
    chain_id: int = 0
    block_number: int = 0
    latency_ms: float = 0.0
    failures: int = 0
    successes: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_checked_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    rate_limit_failures: int = 0
    adaptive_cooldown_s: float = 0.0
    last_cooldown_s: float = 0.0
    dispatch_window_started_at: float = 0.0
    dispatches_in_window: int = 0
    preemptive_request_limit: int = 0

    @property
    def healthy(self) -> bool:
        return self.status == "online" and self.chain_id == POLYGON_CHAIN_ID

    def score(self, highest_block: int, now: float | None = None) -> tuple[int, int, int, float, int, int]:
        now = time.monotonic() if now is None else now
        block_lag = max(highest_block - self.block_number, 0)
        cooling_down = self.cooldown_until > now
        penalty_ms = self.latency_ms or 1_000_000.0
        penalty_ms += min(self.consecutive_failures * 250.0, 5_000.0)
        penalty_ms -= min(self.consecutive_successes * 10.0, 250.0)
        return (
            0 if self.healthy else 1,
            1 if cooling_down else 0,
            block_lag,
            penalty_ms,
            self.failures,
            -self.successes,
        )


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or "${" in value:
        return None
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _dedupe_urls(values: Iterable[Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = _valid_http_url(value)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def collect_rpc_urls(
    *,
    chain_id: int = POLYGON_CHAIN_ID,
    include_discovered: bool = True,
    include_defaults: bool = True,
) -> list[str]:
    """Collect ordered RPC candidates from existing env lanes and discovery."""
    configured: list[str] = []
    for key in RPC_ENV_KEYS:
        configured.append(os.getenv(key, ""))
    extra = os.getenv("RPC_ROTATION_URLS", "")
    configured.extend(part.strip() for part in extra.split(",") if part.strip())
    if include_discovered:
        configured.extend(discover_public_rpc_urls(chain_id))
    if include_defaults:
        configured.extend(DEFAULT_POLYGON_RPC_URLS)
    return _dedupe_urls(configured)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _rate_limit_like(error: Any) -> bool:
    text = str(error).lower()
    return any(
        needle in text
        for needle in (
            "429",
            "too many requests",
            "rate limit",
            "rate-limit",
            "ratelimit",
            "request limit",
            "limit exceeded",
            "quota",
            "throttle",
        )
    )


def _default_state_path() -> Path:
    configured = (os.getenv("RPC_ROTATION_STATE_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "runtime" / "rpc_rotation_state.json"


class RpcRotationManager:
    """Health-scored RPC manager with cooldown and retry rotation."""

    def __init__(
        self,
        urls: Iterable[str] | None = None,
        *,
        chain_id: int = POLYGON_CHAIN_ID,
        timeout_s: float | None = None,
        cooldown_s: float = 8.0,
        max_cooldown_s: float | None = None,
        dispatch_window_s: float | None = None,
        preemptive_request_limit: int | None = None,
        state_path: str | os.PathLike[str] | None = None,
        persist_state: bool | None = None,
        web3_factory: Callable[[str, float], Any] | None = None,
    ) -> None:
        self.chain_id = int(chain_id)
        self.timeout_s = float(timeout_s or os.getenv("RPC_REQUEST_TIMEOUT_SEC", "8") or 8.0)
        self.cooldown_s = float(cooldown_s)
        self.max_cooldown_s = float(max_cooldown_s or os.getenv("RPC_ROTATION_MAX_COOLDOWN_SEC", "180") or 180.0)
        self.dispatch_window_s = float(dispatch_window_s or os.getenv("RPC_ROTATION_DISPATCH_WINDOW_SEC", "60") or 60.0)
        self.preemptive_request_limit = max(
            1,
            int(preemptive_request_limit or os.getenv("RPC_ROTATION_PREEMPTIVE_REQUEST_LIMIT", "80") or 80),
        )
        self.persist_state = _env_bool("RPC_ROTATION_PERSIST_STATE", True) if persist_state is None else bool(persist_state)
        self.state_path = Path(state_path) if state_path is not None else _default_state_path()
        self._web3_factory = web3_factory
        self._cursor = 0
        candidates = list(urls or collect_rpc_urls(chain_id=self.chain_id))
        self._states = {
            url: RpcEndpointState(label=f"rpc_{idx}", url=url)
            for idx, url in enumerate(_dedupe_urls(candidates), start=1)
        }
        if not self._states:
            raise RpcRotationError("No RPC endpoints configured or discovered")
        self._load_state()

    def _load_state(self) -> None:
        if not self.persist_state or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if int(payload.get("chain_id", 0) or 0) not in {0, self.chain_id}:
            return
        now = time.monotonic()
        endpoint_payloads = payload.get("endpoints", {})
        if not isinstance(endpoint_payloads, dict):
            return
        for url, data in endpoint_payloads.items():
            state = self._states.get(url)
            if state is None or not isinstance(data, dict):
                continue
            state.status = str(data.get("status") or state.status)
            state.chain_id = int(data.get("chain_id") or state.chain_id or 0)
            state.block_number = int(data.get("block_number") or state.block_number or 0)
            state.latency_ms = float(data.get("latency_ms") or state.latency_ms or 0.0)
            state.failures = int(data.get("failures") or 0)
            state.successes = int(data.get("successes") or 0)
            state.consecutive_failures = int(data.get("consecutive_failures") or 0)
            state.consecutive_successes = int(data.get("consecutive_successes") or 0)
            state.rate_limit_failures = int(data.get("rate_limit_failures") or 0)
            state.adaptive_cooldown_s = float(data.get("adaptive_cooldown_s") or 0.0)
            state.last_cooldown_s = float(data.get("last_cooldown_s") or 0.0)
            state.last_error = str(data.get("last_error") or "")
            state.last_success_at = float(data.get("last_success_at") or 0.0)
            state.last_failure_at = float(data.get("last_failure_at") or 0.0)
            state.preemptive_request_limit = int(data.get("preemptive_request_limit") or self.preemptive_request_limit)
            remaining = float(data.get("cooldown_remaining_s") or 0.0)
            if remaining > 0.0:
                state.cooldown_until = now + min(remaining, self.max_cooldown_s)

    def _save_state(self) -> None:
        if not self.persist_state:
            return
        now = time.monotonic()
        payload = {
            "version": 1,
            "chain_id": self.chain_id,
            "updated_at": time.time(),
            "endpoints": {
                url: {
                    "status": state.status,
                    "chain_id": state.chain_id,
                    "block_number": state.block_number,
                    "latency_ms": state.latency_ms,
                    "failures": state.failures,
                    "successes": state.successes,
                    "consecutive_failures": state.consecutive_failures,
                    "consecutive_successes": state.consecutive_successes,
                    "rate_limit_failures": state.rate_limit_failures,
                    "adaptive_cooldown_s": state.adaptive_cooldown_s,
                    "last_cooldown_s": state.last_cooldown_s,
                    "last_error": state.last_error,
                    "last_success_at": state.last_success_at,
                    "last_failure_at": state.last_failure_at,
                    "preemptive_request_limit": state.preemptive_request_limit or self.preemptive_request_limit,
                    "cooldown_remaining_s": max(0.0, state.cooldown_until - now),
                }
                for url, state in self._states.items()
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.state_path.parent),
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                tmp_name = handle.name
            os.replace(tmp_name, self.state_path)
        except Exception:
            return

    @property
    def urls(self) -> list[str]:
        return list(self._states)

    def _make_w3(self, url: str) -> Any:
        if self._web3_factory is not None:
            return self._web3_factory(url, self.timeout_s)
        from web3 import Web3

        return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": self.timeout_s}))

    def _probe(self, url: str) -> RpcEndpointState:
        state = self._states[url]
        start = time.perf_counter()
        state.last_checked_at = time.time()
        try:
            w3 = self._make_w3(url)
            if not w3.is_connected():
                raise RpcRotationError("is_connected() returned false")
            chain_id = int(w3.eth.chain_id)
            block_number = int(w3.eth.block_number)
            state.latency_ms = (time.perf_counter() - start) * 1000.0
            state.chain_id = chain_id
            state.block_number = block_number
            if chain_id != self.chain_id:
                state.status = "wrong_chain"
                state.last_error = f"chain_id={chain_id}, expected={self.chain_id}"
                self._record_failure(state, state.last_error, rate_limited=False, multiplier=3.0, status="wrong_chain")
            else:
                state.status = "online"
                self._record_success(state)
        except Exception as exc:  # noqa: BLE001
            state.latency_ms = (time.perf_counter() - start) * 1000.0
            state.status = "error"
            self._record_failure(state, exc)
        return state

    def _endpoint_limit(self, state: RpcEndpointState) -> int:
        return max(1, int(state.preemptive_request_limit or self.preemptive_request_limit))

    def _record_dispatch(self, state: RpcEndpointState) -> bool:
        now = time.monotonic()
        if not state.dispatch_window_started_at or now - state.dispatch_window_started_at >= self.dispatch_window_s:
            state.dispatch_window_started_at = now
            state.dispatches_in_window = 0
        state.dispatches_in_window += 1
        limit = self._endpoint_limit(state)
        if state.dispatches_in_window >= limit:
            learned = max(self.cooldown_s * 0.5, min(state.adaptive_cooldown_s or self.cooldown_s, self.max_cooldown_s))
            state.last_error = f"preemptive rotation after {state.dispatches_in_window}/{limit} dispatches"
            state.last_cooldown_s = learned
            state.cooldown_until = now + learned
            state.dispatch_window_started_at = now
            state.dispatches_in_window = 0
            self._save_state()
            return False
        return True

    def _record_success(self, state: RpcEndpointState) -> None:
        state.status = "online"
        state.last_error = ""
        state.successes += 1
        state.consecutive_successes += 1
        state.consecutive_failures = 0
        state.last_success_at = time.time()
        state.cooldown_until = 0.0
        if state.adaptive_cooldown_s > self.cooldown_s and state.consecutive_successes >= 3:
            state.adaptive_cooldown_s = max(self.cooldown_s, state.adaptive_cooldown_s * 0.85)
        if state.consecutive_successes % 25 == 0:
            state.preemptive_request_limit = min(
                max(self._endpoint_limit(state) + 1, self.preemptive_request_limit),
                max(self.preemptive_request_limit * 4, self.preemptive_request_limit + 1),
            )
        self._save_state()

    def _record_failure(
        self,
        state: RpcEndpointState,
        error: Any,
        *,
        rate_limited: bool | None = None,
        multiplier: float = 1.0,
        status: str | None = None,
    ) -> None:
        now = time.monotonic()
        error_text = str(error).split("\n")[0][:180]
        limited = _rate_limit_like(error_text) if rate_limited is None else rate_limited
        state.status = status or ("rate_limited" if limited else "error")
        state.last_error = error_text
        state.failures += 1
        state.consecutive_failures += 1
        state.consecutive_successes = 0
        state.last_failure_at = time.time()
        if limited:
            state.rate_limit_failures += 1
            observed_limit = state.dispatches_in_window or self._endpoint_limit(state)
            state.preemptive_request_limit = max(1, min(self._endpoint_limit(state), int(max(1, observed_limit) * 0.8)))
            multiplier = max(multiplier, 2.0)
        base = state.adaptive_cooldown_s or self.cooldown_s
        learned = min(
            self.max_cooldown_s,
            max(self.cooldown_s, base * multiplier * (1.35 ** max(0, state.consecutive_failures - 1))),
        )
        state.adaptive_cooldown_s = learned
        state.last_cooldown_s = learned
        state.cooldown_until = now + learned
        state.dispatch_window_started_at = now
        state.dispatches_in_window = 0
        self._save_state()

    def mark_success(self, url: str) -> None:
        state = self._states.get(url)
        if state is None:
            return
        self._record_success(state)

    def refresh(self, *, force: bool = False) -> list[RpcEndpointState]:
        now = time.monotonic()
        for state in self._states.values():
            if force or state.cooldown_until <= now or state.status == "unchecked":
                self._probe(state.url)
        return self.report()

    def report(self) -> list[RpcEndpointState]:
        highest = max((state.block_number for state in self._states.values()), default=0)
        now = time.monotonic()
        return sorted(self._states.values(), key=lambda state: state.score(highest, now))

    def select_url(self) -> str:
        self.refresh()
        ranked = self.report()
        now = time.monotonic()
        healthy = [state for state in ranked if state.healthy and state.cooldown_until <= now]
        if not healthy:
            details = "; ".join(
                f"{state.url} [{state.status}: {state.last_error}]" for state in ranked[:5]
            )
            raise RpcRotationError(f"No healthy Polygon RPC endpoint. {details}")
        for _ in range(len(healthy)):
            selected = healthy[self._cursor % len(healthy)]
            self._cursor += 1
            if self._record_dispatch(selected):
                return selected.url
        return self.select_url()

    def get_web3(self) -> tuple[Any, str]:
        url = self.select_url()
        return self._make_w3(url), url

    def mark_failure(self, url: str, error: Any = "") -> None:
        state = self._states.get(url)
        if state is None:
            return
        self._record_failure(state, error)

    def call(self, method: str, params: list[Any] | None = None, *, attempts: int | None = None) -> Any:
        """Execute a JSON-RPC call, rotating on HTTP or JSON-RPC failures."""
        params = params or []
        attempts = attempts or max(1, len(self._states))
        last_error = ""
        for _ in range(attempts):
            url = self.select_url()
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000, "method": method, "params": params}
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                if "error" in parsed:
                    raise RpcRotationError(str(parsed["error"]))
                self.mark_success(url)
                return parsed.get("result")
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                self.mark_failure(url, exc)
        raise RpcRotationError(f"RPC call {method} failed across rotation: {last_error}")

    def summary(self) -> dict[str, Any]:
        ranked = self.report()
        now = time.monotonic()
        return {
            "chain_id": self.chain_id,
            "healthy_count": sum(1 for state in ranked if state.healthy),
            "endpoint_count": len(ranked),
            "state_path": str(self.state_path) if self.persist_state else "",
            "active_order": [
                {
                    "url": state.url,
                    "status": state.status,
                    "chain_id": state.chain_id,
                    "block_number": state.block_number,
                    "latency_ms": round(state.latency_ms, 3),
                    "failures": state.failures,
                    "successes": state.successes,
                    "consecutive_failures": state.consecutive_failures,
                    "consecutive_successes": state.consecutive_successes,
                    "rate_limit_failures": state.rate_limit_failures,
                    "adaptive_cooldown_s": round(state.adaptive_cooldown_s, 3),
                    "last_cooldown_s": round(state.last_cooldown_s, 3),
                    "cooldown_remaining_s": round(max(0.0, state.cooldown_until - now), 3),
                    "dispatches_in_window": state.dispatches_in_window,
                    "preemptive_request_limit": self._endpoint_limit(state),
                    "last_error": state.last_error,
                }
                for state in ranked
            ],
        }
