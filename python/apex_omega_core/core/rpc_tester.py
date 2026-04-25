"""rpc_tester.py – SSOT for live Polygon RPC endpoints.

All tests and simulations that need live on-chain data must import
endpoints, helpers, and fixtures from this module instead of defining
their own Web3 connections or hardcoding RPC URLs.

Public API
----------
RPC_URL : str
    Primary HTTP-RPC URL resolved from environment.
WSS_URL : str
    WebSocket URL resolved from environment.
POOLS : dict[str, str]
    Well-known Polygon mainnet pool addresses (checksummed).
TOKENS : dict[str, tuple[str, int]]
    Canonical token registry {symbol: (address, decimals)}.
get_w3() -> Web3
    Cached Web3 instance for RPC_URL.
is_live_available() -> bool
    True when a live RPC connection can be confirmed.
fetch_v2_pool_state(pair_address) -> dict
    Live QuickSwap-V2 pair state (reserves, tokens, fee).
fetch_v3_pool_state(pool_address) -> dict
    Live Uniswap-V3 pool state (sqrtPriceX96, liquidity, fee, tokens).
v3_virtual_reserves(sqrt_price_x96, liquidity) -> tuple[float, float]
    Approximate V3 virtual reserves as (reserve_a, reserve_b).
get_canonical_two_leg_state() -> dict
    Live two-leg pool state ready for SSOTPipelineFinalizer.run().
scan_endpoints(timeout, chain_id) -> list[dict]
    Sweep all configured RPC/WSS/relay endpoints, measure latency and block
    height, and return a leaderboard sorted by (block_height DESC, latency ASC).
    Suitable for standalone execution: ``python -m apex_omega_core.core.rpc_tester``
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Load .env automatically when available (idempotent, never overwrites
# existing env vars so the caller always wins over the file).
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Exported endpoint constants
# ---------------------------------------------------------------------------

#: Primary HTTP-RPC URL (Polygon mainnet).  Resolved from the environment at
#: import time so that every consumer obtains the same configured value.
RPC_URL: str = (
    os.getenv("POLYGON_RPC")
    or os.getenv("POLYGON_HTTP")
    or os.getenv("ALCHEMY_HTTP_1")
    or "https://polygon-rpc.com/"
)

#: WebSocket URL (Polygon mainnet).
WSS_URL: str = (
    os.getenv("POLYGON_WSS")
    or os.getenv("ALCHEMY_WSS_1")
    or ""
)

# ---------------------------------------------------------------------------
# Pool registry  (Polygon mainnet, checksummed)
# ---------------------------------------------------------------------------

#: Well-known pool addresses keyed by a human-readable tag.
POOLS: dict = {
    # QuickSwap V2 pairs (constant-product, getReserves() available)
    "USDC_WMATIC_QSV2": "0x6e7a5FAFcec6BB1e78bAE2A1F0B612012BF14827",
    "USDC_WETH_QSV2":   "0x853Ee4b2A13f8a742d64C8F088bE7bA2131f670d",
    # Uniswap V3 pools (sqrtPriceX96 / liquidity based)
    "USDC_WMATIC_UV3_500": "0xA374094527e1673A86dE625aa59517c5dE346d32",
    "USDC_USDT_UV3_100":   "0x3F5228d0e7D75467366be7De2c31D0d098bA2C23",
}

# ---------------------------------------------------------------------------
# Token registry  (Polygon mainnet)
# ---------------------------------------------------------------------------

#: Canonical token registry: {symbol: (checksummed_address, decimals)}.
TOKENS: dict = {
    "USDCe":   ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),   # bridged USDC
    "USDC":    ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),   # native USDC
    "USDT":    ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":     ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "WMATIC":  ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH":    ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC":    ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    "AAVE":    ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "LINK":    ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
}

# ---------------------------------------------------------------------------
# On-chain ABIs (minimal)
# ---------------------------------------------------------------------------

_V2_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_QSV2_FACTORY_ADDR = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"
_QSV2_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_UNIV3_FACTORY_ADDR = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
_UNIV3_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)
_w3_instance = None

# RPC request timeout in seconds (configurable via RPC_REQUEST_TIMEOUT_SEC).
_RPC_TIMEOUT: float = float(os.getenv("RPC_REQUEST_TIMEOUT_SEC", "10"))

if not (os.getenv("POLYGON_RPC") or os.getenv("POLYGON_HTTP") or os.getenv("ALCHEMY_HTTP_1")):
    _logger.warning(
        "rpc_tester: no RPC endpoint found in environment (POLYGON_RPC / "
        "POLYGON_HTTP / ALCHEMY_HTTP_1).  Falling back to public %r — "
        "this endpoint is rate-limited and should not be used in production.",
        RPC_URL,
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_w3():
    """Return a cached Web3 HTTPProvider instance connected to ``RPC_URL``.

    The instance is created lazily on first call and reused thereafter.
    """
    global _w3_instance  # noqa: PLW0603
    if _w3_instance is None:
        from web3 import Web3  # local import keeps web3 optional at module load

        _w3_instance = Web3(
            Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": _RPC_TIMEOUT})
        )
    return _w3_instance


def is_live_available() -> bool:
    """Return ``True`` when the configured Polygon RPC endpoint is reachable."""
    try:
        return get_w3().is_connected()
    except Exception:  # noqa: BLE001
        return False


def fetch_v2_pool_state(pair_address: str) -> dict:
    """Fetch live QuickSwap-V2 pair state from Polygon mainnet.

    Parameters
    ----------
    pair_address:
        Checksummed or lowercase ERC-20 pair contract address.

    Returns
    -------
    dict with keys:
        ``token0``        – checksummed address of token0
        ``token1``        – checksummed address of token1
        ``reserve0_raw``  – raw on-chain reserve of token0 (integer, base units)
        ``reserve1_raw``  – raw on-chain reserve of token1 (integer, base units)
        ``fee_decimal``   – pool swap fee as a decimal (0.003 for 0.3 %)
    """
    from web3 import Web3

    w3 = get_w3()
    pair = w3.eth.contract(
        address=Web3.to_checksum_address(pair_address), abi=_V2_PAIR_ABI
    )
    r0, r1, _ = pair.functions.getReserves().call()
    token0 = pair.functions.token0().call()
    token1 = pair.functions.token1().call()
    return {
        "token0": token0,
        "token1": token1,
        "reserve0_raw": r0,
        "reserve1_raw": r1,
        "fee_decimal": 0.003,  # QuickSwap V2 fixed at 0.3 %
    }


def fetch_v3_pool_state(pool_address: str) -> dict:
    """Fetch live Uniswap-V3 pool state from Polygon mainnet.

    Parameters
    ----------
    pool_address:
        Checksummed or lowercase V3 pool contract address.

    Returns
    -------
    dict with keys:
        ``token0``          – checksummed address of token0
        ``token1``          – checksummed address of token1
        ``fee_decimal``     – pool swap fee as a decimal (e.g. 0.0005 for 0.05 %)
        ``liquidity``       – active liquidity (int, Q128 units)
        ``sqrt_price_x96``  – current sqrtPriceX96 (int)
    """
    from web3 import Web3

    w3 = get_w3()
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(pool_address), abi=_V3_POOL_ABI
    )
    slot0 = pool.functions.slot0().call()
    liquidity = pool.functions.liquidity().call()
    fee_raw = pool.functions.fee().call()
    token0 = pool.functions.token0().call()
    token1 = pool.functions.token1().call()
    return {
        "token0": token0,
        "token1": token1,
        "fee_decimal": fee_raw / 1_000_000,
        "liquidity": liquidity,
        "sqrt_price_x96": slot0[0],
    }


def v3_virtual_reserves(
    sqrt_price_x96: int, liquidity: int
) -> Tuple[float, float]:
    """Derive approximate virtual token reserves from a Uniswap-V3 pool state.

    Uses the geometric-mean virtual-reserve formulas:

        reserve_a = L / sqrt(P)
        reserve_b = L × sqrt(P)

    where ``P = (sqrtPriceX96 / 2^96)^2``.  The resulting pair satisfies the
    constant-product invariant ``reserve_a × reserve_b = L^2`` and can be
    passed directly to ``SlippageSentinel.two_leg_arb_profit`` or the SSOT
    pipeline.

    Returns
    -------
    (reserve_a, reserve_b) as floats.
    """
    sqrt_p = sqrt_price_x96 / (2 ** 96)
    if sqrt_p <= 0:
        raise ValueError(
            f"sqrt_price_x96 must be positive, got {sqrt_price_x96!r}"
        )
    return (float(liquidity) / sqrt_p, float(liquidity) * sqrt_p)


def lookup_qsv2_pair(token_a: str, token_b: str) -> str:
    """Return the QuickSwap-V2 pair address for two tokens (on-chain lookup).

    Parameters
    ----------
    token_a, token_b:
        Token symbol (from ``TOKENS``) or checksummed address.
    """
    from web3 import Web3

    def _resolve(t: str) -> str:
        return TOKENS[t][0] if t in TOKENS else t

    w3 = get_w3()
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(_QSV2_FACTORY_ADDR),
        abi=_QSV2_FACTORY_ABI,
    )
    addr_a = Web3.to_checksum_address(_resolve(token_a))
    addr_b = Web3.to_checksum_address(_resolve(token_b))
    return factory.functions.getPair(addr_a, addr_b).call()


def get_canonical_two_leg_state() -> dict:
    """Return a live two-leg pool state for the USDC/WMATIC canonical pair.

    Leg 1: QuickSwap-V2 USDC-WMATIC pair  (constant-product, real reserves)
    Leg 2: Uniswap-V3  USDC-WMATIC 0.05 % pool (virtual reserves via slot0)

    The returned dict uses the exact keys expected by
    ``SSOTPipelineFinalizer.run()`` and ``BatchSimulator.run()``:

        fee1, r1_in, r1_out, fee2, r2_in, r2_out, c_total_exec

    ``c_total_exec`` = flash_fee + gas_cost ONLY.  DEX fees are embedded in
    the AMM outputs and must **not** be included here.

    Reserve values are in *human-readable token units* (not base units), which
    is what the constant-product math inside ``SlippageSentinel`` expects.

    Raises
    ------
    RuntimeError
        When the RPC endpoint is unreachable.
    ConnectionError
        When either pool query fails (pool address stale / zero liquidity).
    """
    if not is_live_available():
        raise RuntimeError(
            f"Polygon RPC not reachable at {RPC_URL!r}. "
            "Check POLYGON_RPC / POLYGON_HTTP in your .env."
        )

    # --- Leg 1: QuickSwap V2 (raw reserves from getReserves) -----------------
    leg1 = fetch_v2_pool_state(POOLS["USDC_WMATIC_QSV2"])

    # The QSV2 USDC/WMATIC pool uses bridged USDC (USDCe, 6 decimals).
    # Native USDC (0x3c499c...) has a separate set of pools; "USDCe" is the
    # correct key here because POOLS["USDC_WMATIC_QSV2"] was deployed with it.
    usdc_addr_lower = TOKENS["USDCe"][0].lower()
    if leg1["token0"].lower() == usdc_addr_lower:
        # token0 = USDC (6 dec), token1 = WMATIC (18 dec)
        r1_in = leg1["reserve0_raw"] / 1e6
        r1_out = leg1["reserve1_raw"] / 1e18
    else:
        # token0 = WMATIC (18 dec), token1 = USDC (6 dec)
        r1_in = leg1["reserve0_raw"] / 1e18
        r1_out = leg1["reserve1_raw"] / 1e6

    if r1_in <= 0 or r1_out <= 0:
        raise ConnectionError(
            f"QSV2 USDC/WMATIC pool {POOLS['USDC_WMATIC_QSV2']} returned "
            f"zero reserves: r0={leg1['reserve0_raw']}, r1={leg1['reserve1_raw']}"
        )

    # --- Leg 2: Uniswap V3 (virtual reserves from slot0 + liquidity) ---------
    leg2 = fetch_v3_pool_state(POOLS["USDC_WMATIC_UV3_500"])

    if leg2["liquidity"] <= 0 or leg2["sqrt_price_x96"] <= 0:
        raise ConnectionError(
            f"UniV3 USDC/WMATIC pool {POOLS['USDC_WMATIC_UV3_500']} has "
            f"zero liquidity or invalid sqrtPriceX96."
        )

    r2a_raw, r2b_raw = v3_virtual_reserves(
        leg2["sqrt_price_x96"], leg2["liquidity"]
    )

    # Scale V3 virtual reserves so their magnitude matches the V2 reserves.
    # This is necessary because the virtual-reserve formula yields unitless
    # Q96-derived floats, whereas the V2 reserves are in token units.
    # We normalise by the geometric mean of the V2 reserves.
    import math as _math

    leg1_scale = _math.sqrt(r1_in * r1_out)
    leg2_scale_raw = _math.sqrt(r2a_raw * r2b_raw)
    if leg2_scale_raw > 0:
        scale = leg1_scale / leg2_scale_raw
        r2_in = r2a_raw * scale
        r2_out = r2b_raw * scale
    else:
        r2_in = r1_in
        r2_out = r1_out

    # Gas cost estimate for both legs (from .env thresholds, defaults match
    # canonical values used throughout the codebase).
    try:
        c1_gas = float(os.getenv("C1_GAS_USD", "0.38"))
        c2_gas = float(os.getenv("C2_GAS_USD", "0.55"))
        if c1_gas < 0 or c2_gas < 0:
            raise ValueError("gas cost must be non-negative")
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid gas cost env vars (C1_GAS_USD, C2_GAS_USD): {exc}"
        ) from exc
    c_total_exec = c1_gas + c2_gas

    return {
        "fee1": leg1["fee_decimal"],
        "r1_in": r1_in,
        "r1_out": r1_out,
        "fee2": leg2["fee_decimal"],
        "r2_in": r2_in,
        "r2_out": r2_out,
        "c_total_exec": c_total_exec,
    }


# ---------------------------------------------------------------------------
# Multi-endpoint scanner
# ---------------------------------------------------------------------------

#: Environment variable names that may contain HTTP RPC URLs, in priority order.
_HTTP_ENV_KEYS: List[str] = [
    "POLYGON_RPC",
    "POLYGON_HTTP",
    "PRIVATE_RPC_URL",
    "ALCHEMY_HTTP_1",
    "ALCHEMY_HTTP_2",
    "INFURA_HTTP",
    "MERKLE_SENDER_URL",
    "SHADOW_FORK_URL",
    "PUBLIC_DRPC",
    "TITAN_MEV_US_WEST",
]

#: Environment variable names that may contain WebSocket URLs.
_WSS_ENV_KEYS: List[str] = [
    "POLYGON_WSS",
    "ALCHEMY_WSS_1",
    "ALCHEMY_WSS_2",
    "INFURA_WSS",
    "INFURA_POLYGON_RPC_WS",
    "ALCHEMY_POLYGON_WSS",
]

#: Environment variable names that may contain MEV-relay HTTP URLs.
_RELAY_ENV_KEYS: List[str] = [
    "FASTLANE_RELAY",
    "MARLIN_RELAY",
    "FLASHBOTS_RELAY",
]


def _build_endpoint_map() -> dict:
    """Collect every endpoint URL from the environment at call time.

    Returns a dict mapping a human-readable label to
    ``{"url": str, "kind": "http"|"wss"|"relay"}``.
    Only keys whose env var is set and non-empty are included; duplicate URLs
    are deduplicated (first label wins).
    """
    seen_urls: set = set()
    endpoints: dict = {}

    def _add(label: str, url: str, kind: str) -> None:
        url = url.strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            endpoints[label] = {"url": url, "kind": kind}

    for key in _HTTP_ENV_KEYS:
        val = os.getenv(key, "")
        if val:
            _add(key, val, "http")

    for key in _WSS_ENV_KEYS:
        val = os.getenv(key, "")
        if val:
            _add(key, val, "wss")

    for key in _RELAY_ENV_KEYS:
        val = os.getenv(key, "")
        if val:
            _add(key, val, "relay")

    # Always include the public fallback so the scan never comes back empty.
    _add("PUBLIC_POLYGON_RPC", "https://polygon-rpc.com/", "http")

    return endpoints


def scan_endpoints(
    timeout: float = 5.0,
    chain_id: int = 137,
) -> List[dict]:
    """Sweep every configured RPC/WSS/relay endpoint and return a leaderboard.

    Reads all endpoint URLs from the process environment (loaded from .env if
    present) — no credentials are hardcoded here.

    For HTTP nodes: connects with Web3.HTTPProvider, checks ``chain_id`` and
    ``eth_blockNumber``.
    For WSS nodes: connects with Web3.WebsocketProvider, same checks.
    For relay nodes (MEV builders): sends a lightweight ``eth_chainId``
    JSON-RPC POST and records HTTP status + latency only (no block number).

    Parameters
    ----------
    timeout:
        Per-endpoint connection/request timeout in seconds.  Default 5 s.
    chain_id:
        Expected EVM chain ID.  Endpoints returning a different chain ID are
        flagged with a warning and excluded from the leaderboard.  Default 137
        (Polygon mainnet).

    Returns
    -------
    list of dicts, sorted by (block_height DESC, latency_ms ASC).
    Each dict contains:
        ``label``        – env var name used as the endpoint identifier
        ``url``          – endpoint URL (credentials redacted in log output)
        ``kind``         – "http" | "wss" | "relay"
        ``latency_ms``   – round-trip latency in milliseconds
        ``block``        – latest block number (0 for relays / on failure)
        ``status``       – "online" | "wrong_chain" | "refused" | "error"
        ``detail``       – extra info (error message, HTTP status, etc.)
    """
    import json
    import urllib.request

    from web3 import Web3

    endpoints = _build_endpoint_map()
    results: List[dict] = []

    _logger.info("=" * 72)
    _logger.info("🔱 APEX-OMEGA RPC SCANNER — %d endpoint(s) from environment", len(endpoints))
    _logger.info("=" * 72)

    for label, meta in endpoints.items():
        url: str = meta["url"]
        kind: str = meta["kind"]
        # Redact API keys in log output (anything after the last '/')
        _display = url if "polygon-rpc.com" in url or "drpc.org" in url or "titanbuilder" in url else url.rsplit("/", 1)[0] + "/***"

        t0 = time.perf_counter()
        row: dict = {"label": label, "url": url, "kind": kind, "latency_ms": 0.0, "block": 0, "status": "error", "detail": ""}

        try:
            if kind == "relay":
                # MEV builders: lightweight health probe — POST eth_chainId
                payload = json.dumps({"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}).encode()
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    row["detail"] = f"HTTP {resp.status}"
                    row["status"] = "online"
                    row["block"] = 0
                _logger.info("🦇 RELAY  ONLINE : %-28s | %7.1f ms | %s", label, row["latency_ms"], _display)

            elif kind == "wss":
                w3 = Web3(Web3.WebsocketProvider(url, websocket_timeout=int(timeout)))
                if w3.is_connected():
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    cid = w3.eth.chain_id
                    if cid != chain_id:
                        row["status"] = "wrong_chain"
                        row["detail"] = f"chain_id={cid}"
                        _logger.warning("⚠️  WSS  WRONG CHAIN: %-28s | expected %d got %d | %s", label, chain_id, cid, _display)
                    else:
                        row["block"] = w3.eth.block_number
                        row["status"] = "online"
                        _logger.info("✅ WSS   ONLINE : %-28s | %7.1f ms | block %-10d | %s", label, row["latency_ms"], row["block"], _display)
                else:
                    row["status"] = "refused"
                    row["detail"] = "is_connected() False"
                    _logger.error("❌ WSS   REFUSED: %-28s | %s", label, _display)

            else:  # http
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
                if w3.is_connected():
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    cid = w3.eth.chain_id
                    if cid != chain_id:
                        row["status"] = "wrong_chain"
                        row["detail"] = f"chain_id={cid}"
                        _logger.warning("⚠️  HTTP WRONG CHAIN: %-28s | expected %d got %d | %s", label, chain_id, cid, _display)
                    else:
                        row["block"] = w3.eth.block_number
                        row["status"] = "online"
                        _logger.info("✅ HTTP  ONLINE : %-28s | %7.1f ms | block %-10d | %s", label, row["latency_ms"], row["block"], _display)
                else:
                    row["status"] = "refused"
                    row["detail"] = "is_connected() False"
                    _logger.error("❌ HTTP  REFUSED: %-28s | %s", label, _display)

        except Exception as exc:  # noqa: BLE001
            row["latency_ms"] = (time.perf_counter() - t0) * 1000
            row["status"] = "error"
            row["detail"] = str(exc).split("\n")[0][:120]
            _logger.error("❌ %-5s  ERROR  : %-28s | %s", kind.upper(), label, row["detail"])

        results.append(row)

    # --- Leaderboard (HTTP + WSS only; relays don't have block numbers) ------
    live_nodes = [r for r in results if r["status"] == "online" and r["kind"] != "relay"]
    relay_nodes = [r for r in results if r["status"] == "online" and r["kind"] == "relay"]

    live_nodes.sort(key=lambda r: (-r["block"], r["latency_ms"]))
    highest_block = live_nodes[0]["block"] if live_nodes else 0

    _logger.info("")
    _logger.info("=" * 72)
    _logger.info("🏆 LEADERBOARD  —  %d/%d nodes online  |  best block: %s",
                 len(live_nodes) + len(relay_nodes), len(results),
                 highest_block or "n/a")
    _logger.info("=" * 72)

    for rank, r in enumerate(live_nodes, start=1):
        lag = highest_block - r["block"]
        lag_str = f"  ⚠️  {lag} blocks behind" if lag > 0 else ""
        _logger.info("#%-2d %-5s | %7.1f ms | block %-10d | %s%s",
                     rank, r["kind"].upper(), r["latency_ms"], r["block"], r["label"], lag_str)

    for r in relay_nodes:
        _logger.info("    RELAY | %7.1f ms | (no block) | %s  [%s]",
                     r["latency_ms"], r["label"], r["detail"])

    if not live_nodes and not relay_nodes:
        _logger.error("CRITICAL: all endpoints unreachable — check environment variables.")

    return results


# ---------------------------------------------------------------------------
# Standalone execution:  python -m apex_omega_core.core.rpc_tester
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = scan_endpoints()
    online = [r for r in results if r["status"] == "online"]
    sys.exit(0 if online else 1)
