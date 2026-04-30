"""rpc_tester.py – SSOT for live Polygon RPC endpoints."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

try:
    from dotenv import load_dotenv as _load_dotenv
    for _env_path in [Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env", Path(__file__).parent.parent / ".env"]:
        if _env_path.exists():
            _load_dotenv(_env_path, override=False)
except ImportError:
    pass

_RPC_CANDIDATE_KEYS = [
    "POLYGON_RPC",
    "POLYGON_HTTP",
    "PRIVATE_RPC_URL",
    "ALCHEMY_HTTP_1",
    "ALCHEMY_HTTP_2",
    "INFURA_HTTP",
    "SHADOW_FORK_URL",
    "PUBLIC_DRPC",
]

def _candidate_urls() -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for key in _RPC_CANDIDATE_KEYS:
        value = os.getenv(key, "").strip()
        if value and value not in seen:
            urls.append(value)
            seen.add(value)
    if "https://polygon-rpc.com/" not in seen:
        urls.append("https://polygon-rpc.com/")
    return urls

RPC_URL: str = _candidate_urls()[0]
WSS_URL: str = os.getenv("POLYGON_WSS") or os.getenv("ALCHEMY_WSS_1") or ""

POOLS: dict = {
    "USDC_WMATIC_QSV2": "0x6e7a5FAFcec6BB1e78bAE2A1F0B612012BF14827",
    "USDC_WETH_QSV2": "0x853Ee4b2A13f8a742d64C8F088bE7bA2131f670d",
    "USDC_WMATIC_UV3_500": "0xA374094527e1673A86dE625aa59517c5dE346d32",
    "USDC_USDT_UV3_100": "0x3F5228d0e7D75467366be7De2c31D0d098bA2C23",
}

TOKENS: dict = {
    "USDCe": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "USDC": ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    "USDT": ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI": ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH": ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC": ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    "AAVE": ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "LINK": ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
}

_V2_PAIR_ABI = [
    {"inputs": [], "name": "getReserves", "outputs": [{"name": "_reserve0", "type": "uint112"}, {"name": "_reserve1", "type": "uint112"}, {"name": "_blockTimestampLast", "type": "uint32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

_V3_POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"}, {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"}, {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"}, {"name": "unlocked", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "liquidity", "outputs": [{"name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "fee", "outputs": [{"name": "", "type": "uint24"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

_logger = logging.getLogger(__name__)
_w3_instance = None
_RPC_TIMEOUT: float = float(os.getenv("RPC_REQUEST_TIMEOUT_SEC", "10"))


def _connect_w3(url: str):
    from web3 import Web3
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": _RPC_TIMEOUT}))


def _resolve_live_w3():
    last_error = None
    for url in _candidate_urls():
        try:
            w3 = _connect_w3(url)
            if w3.is_connected() and w3.eth.chain_id == 137:
                return url, w3
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"No reachable Polygon RPC found. Last error: {last_error}")


def get_w3():
    global _w3_instance, RPC_URL  # noqa: PLW0603
    if _w3_instance is None:
        RPC_URL, _w3_instance = _resolve_live_w3()
    return _w3_instance


def is_live_available() -> bool:
    try:
        get_w3()
        return True
    except Exception:
        return False


def fetch_v2_pool_state(pair_address: str) -> dict:
    from web3 import Web3
    pair = get_w3().eth.contract(address=Web3.to_checksum_address(pair_address), abi=_V2_PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    return {"token0": pair.functions.token0().call(), "token1": pair.functions.token1().call(), "reserve0_raw": r0, "reserve1_raw": r1, "fee_decimal": 0.003}


def fetch_v3_pool_state(pool_address: str) -> dict:
    from web3 import Web3
    pool = get_w3().eth.contract(address=Web3.to_checksum_address(pool_address), abi=_V3_POOL_ABI)
    slot0 = pool.functions.slot0().call()
    return {"token0": pool.functions.token0().call(), "token1": pool.functions.token1().call(), "fee_decimal": pool.functions.fee().call() / 1_000_000, "liquidity": pool.functions.liquidity().call(), "sqrt_price_x96": slot0[0]}


def v3_virtual_reserves(sqrt_price_x96: int, liquidity: int) -> Tuple[float, float]:
    sqrt_p = sqrt_price_x96 / (2 ** 96)
    if sqrt_p <= 0:
        raise ValueError(f"sqrt_price_x96 must be positive, got {sqrt_price_x96!r}")
    return (float(liquidity) / sqrt_p, float(liquidity) * sqrt_p)


def get_canonical_two_leg_state() -> dict:
    if not is_live_available():
        raise RuntimeError("No Polygon RPC reachable. Check POLYGON_RPC / PRIVATE_RPC_URL / SHADOW_FORK_URL in .env.")

    leg1 = fetch_v2_pool_state(POOLS["USDC_WMATIC_QSV2"])
    usdc_addr_lower = TOKENS["USDCe"][0].lower()
    if leg1["token0"].lower() == usdc_addr_lower:
        r1_in = leg1["reserve0_raw"] / 1e6
        r1_out = leg1["reserve1_raw"] / 1e18
    else:
        r1_in = leg1["reserve0_raw"] / 1e18
        r1_out = leg1["reserve1_raw"] / 1e6

    if r1_in <= 0 or r1_out <= 0:
        raise ConnectionError("QSV2 USDC/WMATIC pool returned zero reserves")

    leg2 = fetch_v3_pool_state(POOLS["USDC_WMATIC_UV3_500"])
    if leg2["liquidity"] <= 0 or leg2["sqrt_price_x96"] <= 0:
        raise ConnectionError("UniV3 USDC/WMATIC pool has zero liquidity or invalid sqrtPriceX96")

    r2a_raw, r2b_raw = v3_virtual_reserves(leg2["sqrt_price_x96"], leg2["liquidity"])
    import math as _math
    leg1_scale = _math.sqrt(r1_in * r1_out)
    leg2_scale_raw = _math.sqrt(r2a_raw * r2b_raw)
    scale = leg1_scale / leg2_scale_raw if leg2_scale_raw > 0 else 1.0

    return {
        "rpc_url": RPC_URL,
        "fee1": leg1["fee_decimal"],
        "r1_in": r1_in,
        "r1_out": r1_out,
        "fee2": leg2["fee_decimal"],
        "r2_in": r2a_raw * scale,
        "r2_out": r2b_raw * scale,
        "c_total_exec": float(os.getenv("C1_GAS_USD", "0.38")) + float(os.getenv("C2_GAS_USD", "0.55")),
    }

_HTTP_ENV_KEYS: List[str] = ["POLYGON_RPC", "POLYGON_HTTP", "PRIVATE_RPC_URL", "ALCHEMY_HTTP_1", "ALCHEMY_HTTP_2", "INFURA_HTTP", "MERKLE_SENDER_URL", "SHADOW_FORK_URL", "PUBLIC_DRPC", "TITAN_MEV_US_WEST"]
_WSS_ENV_KEYS: List[str] = ["POLYGON_WSS", "ALCHEMY_WSS_1", "ALCHEMY_WSS_2", "INFURA_WSS", "INFURA_POLYGON_RPC_WS", "ALCHEMY_POLYGON_WSS"]
_RELAY_ENV_KEYS: List[str] = ["FASTLANE_RELAY", "MARLIN_RELAY", "FLASHBOTS_RELAY"]

def _build_endpoint_map() -> dict:
    seen_urls: set = set()
    endpoints: dict = {}
    def _add(label: str, url: str, kind: str) -> None:
        url = url.strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            endpoints[label] = {"url": url, "kind": kind}
    for key in _HTTP_ENV_KEYS:
        if os.getenv(key, ""):
            _add(key, os.getenv(key, ""), "http")
    for key in _WSS_ENV_KEYS:
        if os.getenv(key, ""):
            _add(key, os.getenv(key, ""), "wss")
    for key in _RELAY_ENV_KEYS:
        if os.getenv(key, ""):
            _add(key, os.getenv(key, ""), "relay")
    _add("PUBLIC_POLYGON_RPC", "https://polygon-rpc.com/", "http")
    return endpoints


def scan_endpoints(timeout: float = 5.0, chain_id: int = 137) -> List[dict]:
    import json
    import urllib.request
    from web3 import Web3
    endpoints = _build_endpoint_map()
    results: List[dict] = []
    _logger.info("=" * 72)
    _logger.info("🔱 APEX-OMEGA RPC SCANNER — %d endpoint(s) from environment", len(endpoints))
    _logger.info("=" * 72)
    for label, meta in endpoints.items():
        url, kind = meta["url"], meta["kind"]
        display = url if "polygon-rpc.com" in url or "drpc.org" in url or "titanbuilder" in url else url.rsplit("/", 1)[0] + "/***"
        t0 = time.perf_counter()
        row = {"label": label, "url": url, "kind": kind, "latency_ms": 0.0, "block": 0, "status": "error", "detail": ""}
        try:
            if kind == "relay":
                payload = json.dumps({"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}).encode()
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "detail": f"HTTP {resp.status}", "status": "online"})
                _logger.info("🦇 RELAY  ONLINE : %-28s | %7.1f ms | %s", label, row["latency_ms"], display)
            elif kind == "wss":
                row["status"] = "error"
                row["detail"] = "WSS scanner disabled for Web3.py v7 compatibility"
                _logger.error("❌ WSS    ERROR  : %-28s | %s", label, row["detail"])
            else:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
                if w3.is_connected() and w3.eth.chain_id == chain_id:
                    row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "block": w3.eth.block_number, "status": "online"})
                    _logger.info("✅ HTTP  ONLINE : %-28s | %7.1f ms | block %-10d | %s", label, row["latency_ms"], row["block"], display)
                else:
                    row["status"] = "refused"
                    row["detail"] = "is_connected() False or wrong chain"
                    _logger.error("❌ HTTP  REFUSED: %-28s | %s", label, display)
        except Exception as exc:
            row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "status": "error", "detail": str(exc).split("\n")[0][:120]})
            _logger.error("❌ %-5s  ERROR  : %-28s | %s", kind.upper(), label, row["detail"])
        results.append(row)
    live_nodes = [r for r in results if r["status"] == "online" and r["kind"] != "relay"]
    relay_nodes = [r for r in results if r["status"] == "online" and r["kind"] == "relay"]
    live_nodes.sort(key=lambda r: (-r["block"], r["latency_ms"]))
    highest_block = live_nodes[0]["block"] if live_nodes else 0
    _logger.info("\n" + "=" * 72)
    _logger.info("🏆 LEADERBOARD  —  %d/%d nodes online  |  best block: %s", len(live_nodes) + len(relay_nodes), len(results), highest_block or "n/a")
    _logger.info("=" * 72)
    for rank, r in enumerate(live_nodes, start=1):
        lag = highest_block - r["block"]
        lag_str = f"  ⚠️  {lag} blocks behind" if lag > 0 else ""
        _logger.info("#%-2d %-5s | %7.1f ms | block %-10d | %s%s", rank, r["kind"].upper(), r["latency_ms"], r["block"], r["label"], lag_str)
    for r in relay_nodes:
        _logger.info("    RELAY | %7.1f ms | (no block) | %s  [%s]", r["latency_ms"], r["label"], r["detail"])
    return results

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = scan_endpoints()
    online = [r for r in results if r["status"] == "online"]
    sys.exit(0 if online else 1)
