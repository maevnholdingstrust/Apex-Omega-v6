from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import aiohttp
except Exception:
    aiohttp = None


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    url: str
    category: str
    role: str
    priority: int = 100


@dataclass
class EndpointResult:
    name: str
    url: str
    category: str
    role: str
    priority: int
    healthy: bool
    latency_ms: float | None
    status: int | None
    error: str | None
    best_use: str
    tested_at: str


ENDPOINT_KEYS = {
    "MEV_RELAY": [
        ("FASTLANE_RELAY", "RELAY_EXECUTION", 10),
        ("FLASHBOTS_RELAY", "RELAY_EXECUTION", 10),
        ("MARLIN_RELAY", "RELAY_EXECUTION", 20),
        ("TITAN_MEV_US_WEST", "RELAY_EXECUTION", 15),
        ("TITAN_MEV_GLOBAL", "RELAY_EXECUTION", 25),
        ("Titan_MEV_Global", "RELAY_EXECUTION", 25),
    ],
    "MEV_WSS": [
        ("TITAN_MEV_WSS", "RELAY_WSS", 20),
        ("Titan_MEV_WSS", "RELAY_WSS", 20),
    ],
    "PRIVATE_RPC_HTTP": [
        ("POLYGON_RPC", "EXECUTION_RPC", 10),
        ("POLYGON_HTTP", "EXECUTION_RPC", 10),
        ("ALCHEMY_HTTP_1", "EXECUTION_RPC", 10),
        ("ALCHEMY_HTTP_2", "EXECUTION_RPC", 15),
        ("INFURA_HTTP", "EXECUTION_RPC", 20),
        ("PRIVATE_RPC_URL", "EXECUTION_RPC", 30),
        ("WEB3_PROVIDER_URI", "EXECUTION_RPC", 30),
    ],
    "PRIVATE_RPC_WSS": [
        ("POLYGON_WSS", "DISCOVERY_WSS", 10),
        ("ALCHEMY_WSS_1", "DISCOVERY_WSS", 10),
        ("ALCHEMY_WSS_2", "DISCOVERY_WSS", 15),
        ("INFURA_WSS", "DISCOVERY_WSS", 20),
    ],
    "PUBLIC_RPC_HTTP": [
        ("PUBLIC_DRPC", "DISCOVERY_RPC", 50),
        ("PUBLIC_1RPC", "DISCOVERY_RPC", 55),
        ("PUBLIC_LLAMA", "DISCOVERY_RPC", 55),
        ("PUBLIC_Llama", "DISCOVERY_RPC", 55),
        ("PUBLIC_ANKR", "DISCOVERY_RPC", 60),
        ("PUBLIC_Ankr", "DISCOVERY_RPC", 60),
        ("PUBLIC_POLYGONRPC", "DISCOVERY_RPC", 80),
        ("PUBLIC_PolygonRPC", "DISCOVERY_RPC", 80),
    ],
    "LOCAL_FORK_RPC": [
        ("FORK_RPC_URL", "FORK_RPC", 5),
    ],
}


def is_public_execution_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or url).lower()
    return host == "polygon-rpc.com" or host.endswith(".polygon-rpc.com")


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        for env_key, env_value in {**values, **os.environ}.items():
            value = value.replace("${" + env_key + "}", env_value)

        values[key] = value

    return values


def best_use(category: str) -> str:
    if category == "PRIVATE_RPC_HTTP":
        return "EXECUTION: gas, reserves, verified reads, transaction submission"
    if category == "PRIVATE_RPC_WSS":
        return "DISCOVERY: live subscriptions, state updates, pending streams where supported"
    if category == "PUBLIC_RPC_HTTP":
        return "DISCOVERY FALLBACK: read-only fallback, not preferred for execution"
    if category == "MEV_RELAY":
        return "RELAY EXECUTION: private submission / bundles, not discovery"
    if category == "MEV_WSS":
        return "RELAY WSS: relay stream or websocket support"
    if category == "LOCAL_FORK_RPC":
        return "FORK SIMULATION ONLY"
    return "CONFIG"


def build_specs(env: dict[str, str]) -> list[EndpointSpec]:
    specs: list[EndpointSpec] = []
    seen: set[tuple[str, str]] = set()

    for category, rows in ENDPOINT_KEYS.items():
        for key, role, priority in rows:
            url = env.get(key) or os.environ.get(key)
            if not url:
                continue
            if key.upper().endswith("AUTH"):
                continue
            if url in ("AUTH_ONLY", "pendingTxs"):
                continue
            if role == "EXECUTION_RPC" and is_public_execution_url(url):
                continue

            dedupe = (key, url)
            if dedupe in seen:
                continue
            seen.add(dedupe)

            specs.append(EndpointSpec(key, url, category, role, priority))

    return specs


def rpc_payload() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}


async def test_http(session: aiohttp.ClientSession, spec: EndpointSpec, timeout_s: float) -> EndpointResult:
    started = time.perf_counter()
    tested_at = utc_now()

    try:
        if spec.category in ("PRIVATE_RPC_HTTP", "PUBLIC_RPC_HTTP", "LOCAL_FORK_RPC"):
            async with session.post(
                spec.url,
                json=rpc_payload(),
                headers={"content-type": "application/json"},
                timeout=timeout_s,
            ) as resp:
                body = await resp.text()
                ms = round((time.perf_counter() - started) * 1000, 2)
                healthy = 200 <= resp.status < 300 and ("result" in body or "jsonrpc" in body)
                return EndpointResult(
                    spec.name, spec.url, spec.category, spec.role, spec.priority,
                    healthy, ms, resp.status, None if healthy else body[:180],
                    best_use(spec.category), tested_at
                )

        async with session.get(spec.url, timeout=timeout_s) as resp:
            await resp.text()
            ms = round((time.perf_counter() - started) * 1000, 2)
            healthy = resp.status < 500
            return EndpointResult(
                spec.name, spec.url, spec.category, spec.role, spec.priority,
                healthy, ms, resp.status, None if healthy else "HTTP " + str(resp.status),
                best_use(spec.category), tested_at
            )

    except Exception as exc:
        ms = round((time.perf_counter() - started) * 1000, 2)
        return EndpointResult(
            spec.name, spec.url, spec.category, spec.role, spec.priority,
            False, ms, None, repr(exc)[:240], best_use(spec.category), tested_at
        )


async def test_wss(spec: EndpointSpec, timeout_s: float) -> EndpointResult:
    started = time.perf_counter()
    tested_at = utc_now()
    parsed = urlparse(spec.url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)

    try:
        ssl_ctx = ssl.create_default_context() if parsed.scheme == "wss" else None
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=host, port=port, ssl=ssl_ctx),
            timeout=timeout_s,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        ms = round((time.perf_counter() - started) * 1000, 2)
        return EndpointResult(
            spec.name, spec.url, spec.category, spec.role, spec.priority,
            True, ms, None, None, best_use(spec.category), tested_at
        )
    except Exception as exc:
        ms = round((time.perf_counter() - started) * 1000, 2)
        return EndpointResult(
            spec.name, spec.url, spec.category, spec.role, spec.priority,
            False, ms, None, repr(exc)[:240], best_use(spec.category), tested_at
        )


async def test_one(spec: EndpointSpec, session: aiohttp.ClientSession, timeout_s: float) -> EndpointResult:
    if spec.url.startswith("wss://") or spec.url.startswith("ws://"):
        return await test_wss(spec, timeout_s)
    return await test_http(session, spec, timeout_s)


def rank_key(result: EndpointResult) -> tuple[int, int, float]:
    return (
        0 if result.healthy else 1,
        result.priority,
        result.latency_ms if result.latency_ms is not None else 999999.0,
    )


async def run() -> dict[str, Any]:
    if aiohttp is None:
        raise RuntimeError("aiohttp missing. Install with: pip install aiohttp")

    env = load_dotenv(Path(".env"))
    specs = build_specs(env)

    timeout_s = float(env.get("ENDPOINT_TEST_TIMEOUT_SECONDS", os.getenv("ENDPOINT_TEST_TIMEOUT_SECONDS", "4.0")))
    concurrency = int(env.get("ENDPOINT_TEST_CONCURRENCY", os.getenv("ENDPOINT_TEST_CONCURRENCY", "12")))

    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=timeout_s + 1.0)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def guarded(spec: EndpointSpec) -> EndpointResult:
            async with sem:
                return await test_one(spec, session, timeout_s)

        results = await asyncio.gather(*(guarded(spec) for spec in specs))

    selected: dict[str, EndpointResult] = {}
    for result in sorted(results, key=rank_key):
        if not result.healthy:
            continue
        selected.setdefault(result.role, result)

    return {
        "generated_at": utc_now(),
        "results": [asdict(r) for r in sorted(results, key=lambda r: (r.category, r.role, rank_key(r)))],
        "selected": {role: asdict(result) for role, result in selected.items()},
    }


def make_env(payload: dict[str, Any]) -> str:
    selected = payload.get("selected", {})
    lines = [
        "# Generated by endpoint_latency_monitor.py",
        "# Runtime-only endpoint selections. Do not commit.",
        "ENDPOINT_SELECTION_GENERATED_AT=" + payload["generated_at"],
        "",
    ]

    execution = selected.get("EXECUTION_RPC")
    discovery_rpc = selected.get("DISCOVERY_RPC")
    discovery_wss = selected.get("DISCOVERY_WSS")
    relay = selected.get("RELAY_EXECUTION")
    relay_wss = selected.get("RELAY_WSS")
    fork = selected.get("FORK_RPC")

    if execution:
        url = execution["url"]
        lines.extend([
            "ACTIVE_EXECUTION_RPC=" + url,
            "POLYGON_RPC_URL=" + url,
            "WEB3_PROVIDER_URI=" + url,
            "PRIVATE_RPC_URL=" + url,
        ])

    if discovery_rpc:
        lines.append("ACTIVE_DISCOVERY_RPC=" + discovery_rpc["url"])

    if discovery_wss:
        url = discovery_wss["url"]
        lines.extend([
            "ACTIVE_DISCOVERY_WSS=" + url,
            "POLYGON_WSS_ACTIVE=" + url,
        ])

    if relay:
        lines.append("ACTIVE_PRIVATE_RELAY=" + relay["url"])

    if relay_wss:
        lines.append("ACTIVE_RELAY_WSS=" + relay_wss["url"])

    if fork:
        lines.append("ACTIVE_FORK_RPC_URL=" + fork["url"])

    return "\n".join(lines) + "\n"


def print_report(payload: dict[str, Any]) -> None:
    print("")
    print("=== APEX ENDPOINT LATENCY REPORT ===")
    print("generated_at=" + payload["generated_at"])

    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in payload["results"]:
        by_category.setdefault(row["category"], []).append(row)

    for category, rows in by_category.items():
        print("")
        print("[" + category + "]")
        for r in rows:
            health = "OK" if r["healthy"] else "FAIL"
            latency = str(r["latency_ms"]) + "ms" if r["latency_ms"] is not None else "n/a"
            print(f"  {health:4} {r['name']:<22} {latency:<12} role={r['role']} status={r['status']}")

    print("")
    print("=== SELECTED FASTEST PER ROLE ===")
    for role, r in payload["selected"].items():
        print(f"  {role:<18} -> {r['name']} ({r['latency_ms']}ms)")

    print("")
    print("=== BOTTOM LINE ===")
    print("  EXECUTION = fastest healthy private HTTP RPC.")
    print("  DISCOVERY = WSS first, public HTTP only as fallback reads.")
    print("  RELAY = private relay submission, not discovery.")
    print("  FORK = local simulation only.")


def main() -> int:
    runtime = Path("runtime")
    runtime.mkdir(parents=True, exist_ok=True)

    payload = asyncio.run(run())

    report_path = runtime / "endpoint_latency_report.json"
    env_path = runtime / "active_endpoints.env"

    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    env_path.write_text(make_env(payload), encoding="utf-8")

    print_report(payload)
    print("")
    print("[WRITE] " + str(report_path))
    print("[WRITE] " + str(env_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
