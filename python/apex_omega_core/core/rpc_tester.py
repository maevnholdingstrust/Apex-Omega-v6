"""rpc_tester.py – SSOT for live Polygon RPC endpoints."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv as _load_dotenv
    for _env_path in [Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env", Path(__file__).parent.parent / ".env"]:
        if _env_path.exists():
            _load_dotenv(_env_path, override=False)
except ImportError:
    pass

_RPC_CANDIDATE_KEYS = ["POLYGON_RPC", "POLYGON_HTTP", "PRIVATE_RPC_URL", "ALCHEMY_HTTP_1", "ALCHEMY_HTTP_2", "INFURA_HTTP", "SHADOW_FORK_URL", "PUBLIC_DRPC"]

def _candidate_urls() -> list[str]:
    urls, seen = [], set()
    for key in _RPC_CANDIDATE_KEYS:
        value = os.getenv(key, "").strip()
        if value and value not in seen:
            urls.append(value); seen.add(value)
    if "https://polygon-rpc.com/" not in seen:
        urls.append("https://polygon-rpc.com/")
    return urls

RPC_URL: str = _candidate_urls()[0]
WSS_URL: str = os.getenv("POLYGON_WSS") or os.getenv("ALCHEMY_WSS_1") or ""

POOLS = {"USDC_WMATIC_QSV2": "0x6e7a5FAFcec6BB1e78bAE2A1F0B612012BF14827", "USDC_WMATIC_UV3_500": "0xA374094527e1673A86dE625aa59517c5dE346d32"}
TOKENS = {"USDCe": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6), "USDC": ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6), "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18)}

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
_RPC_TIMEOUT = float(os.getenv("RPC_REQUEST_TIMEOUT_SEC", "10"))

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
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No reachable Polygon RPC found. Last error: {last_error}")

def get_w3():
    global _w3_instance, RPC_URL
    if _w3_instance is None:
        RPC_URL, _w3_instance = _resolve_live_w3()
    return _w3_instance

def is_live_available() -> bool:
    try: get_w3(); return True
    except Exception: return False

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

def token_decimals(address: str) -> int:
    lower = address.lower()
    for _, (addr, dec) in TOKENS.items():
        if addr.lower() == lower:
            return dec
    return 18

def v3_price_token1_per_token0(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    raw_price = (sqrt_price_x96 / (2 ** 96)) ** 2
    return raw_price * (10 ** (dec0 - dec1))

def get_canonical_two_leg_state() -> dict:
    if not is_live_available():
        raise RuntimeError("No Polygon RPC reachable. Check POLYGON_RPC / PRIVATE_RPC_URL / SHADOW_FORK_URL in .env.")

    leg1 = fetch_v2_pool_state(POOLS["USDC_WMATIC_QSV2"])
    usdc_e = TOKENS["USDCe"][0].lower(); wmatic = TOKENS["WMATIC"][0].lower()
    if leg1["token0"].lower() == usdc_e:
        qsv2_usdc = leg1["reserve0_raw"] / 1e6; qsv2_wmatic = leg1["reserve1_raw"] / 1e18
    else:
        qsv2_usdc = leg1["reserve1_raw"] / 1e6; qsv2_wmatic = leg1["reserve0_raw"] / 1e18
    qsv2_wmatic_per_usdc = qsv2_wmatic / qsv2_usdc
    qsv2_usdc_per_wmatic = qsv2_usdc / qsv2_wmatic

    leg2 = fetch_v3_pool_state(POOLS["USDC_WMATIC_UV3_500"])
    dec0, dec1 = token_decimals(leg2["token0"]), token_decimals(leg2["token1"])
    token1_per_token0 = v3_price_token1_per_token0(leg2["sqrt_price_x96"], dec0, dec1)
    if leg2["token0"].lower() in {TOKENS["USDCe"][0].lower(), TOKENS["USDC"][0].lower()}:
        uv3_wmatic_per_usdc = token1_per_token0
        uv3_usdc_per_wmatic = 1 / token1_per_token0 if token1_per_token0 else 0.0
    else:
        uv3_usdc_per_wmatic = token1_per_token0
        uv3_wmatic_per_usdc = 1 / token1_per_token0 if token1_per_token0 else 0.0

    # Direction tested by canonical dry-run: USDC -> WMATIC on QSV2, then WMATIC -> USDC on UV3.
    # For CPMM-compatible diagnostic math, synthesize UV3 reserves from QSV2 scale and true UV3 spot price.
    r1_in, r1_out = qsv2_usdc, qsv2_wmatic
    r2_in = qsv2_wmatic
    r2_out = qsv2_wmatic * uv3_usdc_per_wmatic

    return {
        "rpc_url": RPC_URL,
        "fee1": leg1["fee_decimal"], "r1_in": r1_in, "r1_out": r1_out,
        "fee2": leg2["fee_decimal"], "r2_in": r2_in, "r2_out": r2_out,
        "qsv2_usdc_per_wmatic": qsv2_usdc_per_wmatic,
        "qsv2_wmatic_per_usdc": qsv2_wmatic_per_usdc,
        "uv3_usdc_per_wmatic": uv3_usdc_per_wmatic,
        "uv3_wmatic_per_usdc": uv3_wmatic_per_usdc,
        "raw_spread_usdc_per_wmatic": uv3_usdc_per_wmatic - qsv2_usdc_per_wmatic,
        "raw_spread_bps": ((uv3_usdc_per_wmatic - qsv2_usdc_per_wmatic) / qsv2_usdc_per_wmatic) * 10_000 if qsv2_usdc_per_wmatic else 0.0,
        "c_total_exec": float(os.getenv("C1_GAS_USD", "0.38")) + float(os.getenv("C2_GAS_USD", "0.55")),
    }

_HTTP_ENV_KEYS = ["POLYGON_RPC", "POLYGON_HTTP", "PRIVATE_RPC_URL", "ALCHEMY_HTTP_1", "ALCHEMY_HTTP_2", "INFURA_HTTP", "MERKLE_SENDER_URL", "SHADOW_FORK_URL", "PUBLIC_DRPC", "TITAN_MEV_US_WEST"]
_WSS_ENV_KEYS = ["POLYGON_WSS", "ALCHEMY_WSS_1", "ALCHEMY_WSS_2", "INFURA_WSS", "INFURA_POLYGON_RPC_WS", "ALCHEMY_POLYGON_WSS"]
_RELAY_ENV_KEYS = ["FASTLANE_RELAY", "MARLIN_RELAY", "FLASHBOTS_RELAY"]

def _build_endpoint_map() -> dict:
    seen_urls, endpoints = set(), {}
    def _add(label: str, url: str, kind: str) -> None:
        url = url.strip()
        if url and url not in seen_urls:
            seen_urls.add(url); endpoints[label] = {"url": url, "kind": kind}
    for key in _HTTP_ENV_KEYS:
        if os.getenv(key, ""): _add(key, os.getenv(key, ""), "http")
    for key in _WSS_ENV_KEYS:
        if os.getenv(key, ""): _add(key, os.getenv(key, ""), "wss")
    for key in _RELAY_ENV_KEYS:
        if os.getenv(key, ""): _add(key, os.getenv(key, ""), "relay")
    _add("PUBLIC_POLYGON_RPC", "https://polygon-rpc.com/", "http")
    return endpoints

def scan_endpoints(timeout: float = 5.0, chain_id: int = 137) -> List[dict]:
    import json, urllib.request
    from web3 import Web3
    endpoints, results = _build_endpoint_map(), []
    _logger.info("=" * 72); _logger.info("🔱 APEX-OMEGA RPC SCANNER — %d endpoint(s) from environment", len(endpoints)); _logger.info("=" * 72)
    for label, meta in endpoints.items():
        url, kind = meta["url"], meta["kind"]
        display = url if "polygon-rpc.com" in url or "drpc.org" in url or "titanbuilder" in url else url.rsplit("/", 1)[0] + "/***"
        t0 = time.perf_counter(); row = {"label": label, "url": url, "kind": kind, "latency_ms": 0.0, "block": 0, "status": "error", "detail": ""}
        try:
            if kind == "relay":
                payload = json.dumps({"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}).encode(); req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as resp: row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "detail": f"HTTP {resp.status}", "status": "online"})
                _logger.info("🦇 RELAY  ONLINE : %-28s | %7.1f ms | %s", label, row["latency_ms"], display)
            elif kind == "wss":
                row.update({"status":"error", "detail":"WSS scanner disabled for Web3.py v7 compatibility"}); _logger.error("❌ WSS    ERROR  : %-28s | %s", label, row["detail"])
            else:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
                if w3.is_connected() and w3.eth.chain_id == chain_id:
                    row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "block": w3.eth.block_number, "status": "online"}); _logger.info("✅ HTTP  ONLINE : %-28s | %7.1f ms | block %-10d | %s", label, row["latency_ms"], row["block"], display)
                else:
                    row.update({"status":"refused", "detail":"is_connected() False or wrong chain"}); _logger.error("❌ HTTP  REFUSED: %-28s | %s", label, display)
        except Exception as exc:
            row.update({"latency_ms": (time.perf_counter() - t0) * 1000, "status":"error", "detail":str(exc).split("\n")[0][:120]}); _logger.error("❌ %-5s  ERROR  : %-28s | %s", kind.upper(), label, row["detail"])
        results.append(row)
    live_nodes = [r for r in results if r["status"] == "online" and r["kind"] != "relay"]; relay_nodes = [r for r in results if r["status"] == "online" and r["kind"] == "relay"]
    live_nodes.sort(key=lambda r: (-r["block"], r["latency_ms"])); highest_block = live_nodes[0]["block"] if live_nodes else 0
    _logger.info("\n" + "=" * 72); _logger.info("🏆 LEADERBOARD  —  %d/%d nodes online  |  best block: %s", len(live_nodes)+len(relay_nodes), len(results), highest_block or "n/a"); _logger.info("=" * 72)
    for rank, r in enumerate(live_nodes, 1):
        lag = highest_block - r["block"]; _logger.info("#%-2d %-5s | %7.1f ms | block %-10d | %s%s", rank, r["kind"].upper(), r["latency_ms"], r["block"], r["label"], f"  ⚠️  {lag} blocks behind" if lag > 0 else "")
    for r in relay_nodes: _logger.info("    RELAY | %7.1f ms | (no block) | %s  [%s]", r["latency_ms"], r["label"], r["detail"])
    return results

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = scan_endpoints(); online = [r for r in results if r["status"] == "online"]
    sys.exit(0 if online else 1)
