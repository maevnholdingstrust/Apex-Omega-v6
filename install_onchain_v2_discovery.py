from pathlib import Path
from datetime import datetime
import shutil
import re
import subprocess
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

core_dir = ROOT / "python" / "apex_omega_core" / "core"
adapter_path = core_dir / "onchain_v2_discovery.py"
arb_path = core_dir / "polygon_arbitrage.py"

if not core_dir.exists():
    raise FileNotFoundError(core_dir)
if not arb_path.exists():
    raise FileNotFoundError(arb_path)

def backup(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + f".bak_onchain_v2_{STAMP}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] {path} -> {bak}")

adapter_code = r'''
"""
Apex on-chain V2 pool discovery.

Primary purpose:
- replace DEXScreener as the main pool-discovery source
- use selected RPC endpoints from boot-time latency monitor
- query V2 factories directly:
    factory.getPair(tokenA, tokenB)
    pair.getReserves()
    pair.token0()
    pair.token1()

Policy:
- DEXScreener is optional enrichment only
- execution quotes must use on-chain reserves or verified pool state
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

import aiohttp


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Common Polygon V2 factory defaults. Existing repo config can override/extend these.
DEFAULT_V2_FACTORIES: dict[str, str] = {
    "quickswap": "0x5757371414417b8c6caad45baef941abc7d3ab32",
    "sushiswap": "0xc35dadb65012ec5796536bd9864ed8773abc74c4",
    "apeswap": "0xcf083be4164828f00cae704ec15a36d711491284",
    "dfyn": "0xe7fb3e833efe5f9c441105eb65ef8b261266423b",
    "jetswap": "0x668ad0ed262ba202188a8d8ff40c1c3f4f5b8bcb",
}

# Function selectors.
SEL_GET_PAIR = "0xe6a43905"
SEL_GET_RESERVES = "0x0902f1ac"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"


@dataclass(frozen=True)
class OnchainV2Pool:
    dex_name: str
    factory: str
    pair_address: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    block_number: int | None = None


def _strip_0x(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _addr_word(address: str) -> str:
    raw = _strip_0x(address.lower())
    if len(raw) != 40:
        raise ValueError(f"invalid address: {address}")
    return raw.rjust(64, "0")


def encode_get_pair(token_a: str, token_b: str) -> str:
    return "0x" + _strip_0x(SEL_GET_PAIR) + _addr_word(token_a) + _addr_word(token_b)


def decode_address_word(result_hex: str) -> str | None:
    if not result_hex or result_hex == "0x":
        return None
    raw = _strip_0x(result_hex)
    if len(raw) < 64:
        return None
    addr = "0x" + raw[-40:]
    if addr.lower() == ZERO_ADDRESS.lower():
        return None
    return addr


def decode_uint256_word(word: str) -> int:
    return int(word, 16)


def decode_get_reserves(result_hex: str) -> tuple[int, int] | None:
    if not result_hex or result_hex == "0x":
        return None
    raw = _strip_0x(result_hex)
    if len(raw) < 64 * 3:
        return None
    reserve0 = decode_uint256_word(raw[0:64])
    reserve1 = decode_uint256_word(raw[64:128])
    return reserve0, reserve1


class RpcClient:
    def __init__(self, rpc_url: str, timeout_s: float = 8.0):
        self.rpc_url = rpc_url
        self.timeout_s = timeout_s
        self._id = 0

    async def call(self, session: aiohttp.ClientSession, to: str, data: str) -> str | None:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }

        try:
            async with session.post(self.rpc_url, json=payload, timeout=self.timeout_s) as resp:
                body = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    return None
                parsed = json.loads(body)
                if "error" in parsed:
                    return None
                return parsed.get("result")
        except Exception:
            return None

    async def block_number(self, session: aiohttp.ClientSession) -> int | None:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": "eth_blockNumber", "params": []}
        try:
            async with session.post(self.rpc_url, json=payload, timeout=self.timeout_s) as resp:
                parsed = json.loads(await resp.text())
                result = parsed.get("result")
                return int(result, 16) if isinstance(result, str) else None
        except Exception:
            return None


def _env_rpc_url() -> str:
    return (
        os.getenv("ACTIVE_DISCOVERY_RPC")
        or os.getenv("ACTIVE_EXECUTION_RPC")
        or os.getenv("POLYGON_RPC_URL")
        or os.getenv("WEB3_PROVIDER_URI")
        or os.getenv("PRIVATE_RPC_URL")
        or ""
    )


def _normalize_address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("0x") and len(value) == 42:
        return value
    return None


def extract_token_address(token: Any) -> str | None:
    if isinstance(token, str):
        return _normalize_address(token)

    for attr in ("address", "token_address", "contract_address"):
        if hasattr(token, attr):
            addr = _normalize_address(getattr(token, attr))
            if addr:
                return addr

    if isinstance(token, dict):
        for key in ("address", "token_address", "contract_address"):
            addr = _normalize_address(token.get(key))
            if addr:
                return addr

    return None


def normalize_factories(factories: dict[str, Any] | None = None) -> dict[str, str]:
    merged = dict(DEFAULT_V2_FACTORIES)
    if factories:
        for name, address in factories.items():
            addr = _normalize_address(str(address))
            if addr:
                merged[str(name).lower()] = addr
    return merged


async def discover_v2_pools_onchain(
    tokens: Iterable[Any],
    factories: dict[str, Any] | None = None,
    rpc_url: str | None = None,
    max_tokens: int | None = None,
    max_pairs: int | None = None,
    concurrency: int | None = None,
) -> list[OnchainV2Pool]:
    """Discover V2 pools directly from factories using eth_call."""
    rpc_url = rpc_url or _env_rpc_url()
    if not rpc_url:
        return []

    token_addresses: list[str] = []
    seen = set()

    for token in tokens:
        addr = extract_token_address(token)
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        token_addresses.append(addr)

    max_tokens = int(max_tokens or os.getenv("ONCHAIN_DISCOVERY_MAX_TOKENS", "80"))
    max_pairs = int(max_pairs or os.getenv("ONCHAIN_DISCOVERY_MAX_PAIRS", "2500"))
    concurrency = int(concurrency or os.getenv("ONCHAIN_DISCOVERY_CONCURRENCY", "32"))

    token_addresses = token_addresses[:max_tokens]
    token_pairs = list(combinations(token_addresses, 2))[:max_pairs]

    factory_map = normalize_factories(factories)
    client = RpcClient(rpc_url)
    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=client.timeout_s + 2.0)
    results: list[OnchainV2Pool] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        block_number = await client.block_number(session)

        async def check_pair(dex_name: str, factory: str, token_a: str, token_b: str):
            async with sem:
                pair_result = await client.call(session, factory, encode_get_pair(token_a, token_b))
                pair = decode_address_word(pair_result or "")
                if not pair:
                    return

                reserves_raw = await client.call(session, pair, SEL_GET_RESERVES)
                reserves = decode_get_reserves(reserves_raw or "")
                if not reserves:
                    return

                token0_raw = await client.call(session, pair, SEL_TOKEN0)
                token1_raw = await client.call(session, pair, SEL_TOKEN1)
                token0 = decode_address_word(token0_raw or "")
                token1 = decode_address_word(token1_raw or "")

                if not token0 or not token1:
                    token0, token1 = token_a, token_b

                reserve0, reserve1 = reserves
                if reserve0 <= 0 or reserve1 <= 0:
                    return

                results.append(
                    OnchainV2Pool(
                        dex_name=dex_name,
                        factory=factory,
                        pair_address=pair,
                        token0=token0,
                        token1=token1,
                        reserve0=reserve0,
                        reserve1=reserve1,
                        block_number=block_number,
                    )
                )

        tasks = [
            check_pair(dex_name, factory, token_a, token_b)
            for dex_name, factory in factory_map.items()
            for token_a, token_b in token_pairs
        ]

        await asyncio.gather(*tasks)

    return results
'''

backup(adapter_path)
adapter_path.write_text(adapter_code, encoding="utf-8", newline="\n")
print(f"[WRITE] {adapter_path}")

backup(arb_path)
text = arb_path.read_text(encoding="utf-8", errors="replace")

# Ensure import.
if "from .onchain_v2_discovery import discover_v2_pools_onchain" not in text:
    marker = "import aiohttp"
    if marker in text:
        text = text.replace(marker, marker + "\nfrom .onchain_v2_discovery import discover_v2_pools_onchain, OnchainV2Pool")
    else:
        text = "from .onchain_v2_discovery import discover_v2_pools_onchain, OnchainV2Pool\n" + text
    print("[PATCH] Added on-chain V2 discovery import")

# Ensure os import.
if "import os" not in text:
    text = "import os\n" + text

# Add config around token pool cache.
cache_line = "        self._token_pool_cache: Dict[str, List[Dict[str, Any]]] = {}"
if cache_line in text and "self.discovery_source" not in text:
    text = text.replace(
        cache_line,
        cache_line + '''
        self.discovery_source: str = os.getenv("DISCOVERY_SOURCE", "onchain_v2").lower()
        self.use_dexscreener: bool = os.getenv("USE_DEXSCREENER", "false").lower() == "true"
        self.onchain_discovery_max_tokens: int = int(os.getenv("ONCHAIN_DISCOVERY_MAX_TOKENS", "80"))
        self.onchain_discovery_max_pairs: int = int(os.getenv("ONCHAIN_DISCOVERY_MAX_PAIRS", "2500"))
        self.onchain_discovery_concurrency: int = int(os.getenv("ONCHAIN_DISCOVERY_CONCURRENCY", "32"))'''
    )
    print("[PATCH] Added discovery source config")

# Add converter method before _scan_dex_pools if not present.
if "_pool_from_onchain_v2" not in text:
    insert_before = "    async def _scan_dex_pools"
    method = r'''
    def _pool_from_onchain_v2(self, raw: OnchainV2Pool) -> Pool:
        """Convert on-chain V2 discovery result into repo Pool model."""
        reserve0 = float(raw.reserve0)
        reserve1 = float(raw.reserve1)

        # Conservative placeholder TVL until token decimals/prices are attached.
        # Execution still requires reserve verification downstream.
        tvl_usd = 0.0

        return Pool(
            address=raw.pair_address,
            dex=raw.dex_name,
            token0=raw.token0,
            token1=raw.token1,
            reserve0=reserve0,
            reserve1=reserve1,
            tvl_usd=tvl_usd,
            fee_bps=30,
            pool_type="v2",
        )

'''
    if insert_before in text:
        text = text.replace(insert_before, method + insert_before)
        print("[PATCH] Added on-chain V2 Pool converter")
    else:
        print("[WARN] Could not locate _scan_dex_pools insertion point")

# Patch scan_all_dexes to use on-chain V2 first.
pattern = re.compile(
    r'''    async def scan_all_dexes\(self, tokens: List\[Any\]\) -> List\[Pool\]:
        .*?
        return all_pools
''',
    re.DOTALL,
)

replacement = r'''    async def scan_all_dexes(self, tokens: List[Any]) -> List[Pool]:
        """Scan all DEXes for pools containing specified tokens.

        Primary policy:
        - onchain_v2 is the default source
        - DEXScreener is optional fallback/enrichment only
        - execution must never depend on DEXScreener priceUsd
        """
        if not self.token_metadata:
            await self.refresh_market_registry()

        self.scanner_errors = 0
        self.last_scan_terminal_state = "SCANNING"

        normalized_tokens = self._normalize_tokens(tokens)
        all_pools: List[Pool] = []

        if not normalized_tokens:
            self.last_scan_terminal_state = "NO_VALID_TOKENS"
            logger.warning("No valid normalized tokens available for DEX scan")
            return []

        if getattr(self, "discovery_source", "onchain_v2") in ("onchain", "onchain_v2", "rpc"):
            try:
                raw_pools = await discover_v2_pools_onchain(
                    normalized_tokens,
                    factories=getattr(self, "_all_dexes", {}),
                    max_tokens=getattr(self, "onchain_discovery_max_tokens", 80),
                    max_pairs=getattr(self, "onchain_discovery_max_pairs", 2500),
                    concurrency=getattr(self, "onchain_discovery_concurrency", 32),
                )
                all_pools = [self._pool_from_onchain_v2(p) for p in raw_pools]
                self.last_scan_terminal_state = "POOLS_DISCOVERED" if all_pools else "NO_POOLS_DISCOVERED"
                logger.info(
                    "On-chain V2 discovery terminal_state=%s pools=%d source=%s",
                    self.last_scan_terminal_state,
                    len(all_pools),
                    self.discovery_source,
                )
                return all_pools
            except Exception as exc:
                self.scanner_errors += 1
                logger.exception("On-chain V2 discovery failed: %s", exc)
                if not getattr(self, "use_dexscreener", False):
                    self.last_scan_terminal_state = "REJECTED_BY_ONCHAIN_DISCOVERY_FAILURE"
                    return []

        if not getattr(self, "use_dexscreener", False):
            self.last_scan_terminal_state = "NO_POOLS_DISCOVERED"
            logger.warning("DEXScreener disabled and on-chain discovery returned no pools")
            return []

        logger.warning("Using DEXScreener fallback because USE_DEXSCREENER=true")

        for dex_name, factory_address in self._all_dexes.items():
            if not dex_name or not factory_address:
                continue

            try:
                pools = await self._scan_dex_pools(dex_name, factory_address, normalized_tokens)

                if pools is None:
                    self.scanner_errors += 1
                    logger.warning("DEX %s returned None; treating as empty pool list", dex_name)
                    pools = []

                if not isinstance(pools, list):
                    try:
                        pools = list(pools)
                    except Exception as exc:
                        self.scanner_errors += 1
                        logger.exception("DEX %s returned non-list/non-iterable pool result: %s", dex_name, exc)
                        pools = []

                all_pools.extend(pools)
                logger.info("Scanned %d pools on %s", len(pools), dex_name)

            except Exception as e:
                self.scanner_errors += 1
                logger.exception("Error scanning %s: %s", dex_name, e)

        if self.scanner_errors > 0 and len(all_pools) == 0:
            self.last_scan_terminal_state = "REJECTED_BY_DATA_INTAKE_FAILURE"
        elif len(all_pools) == 0:
            self.last_scan_terminal_state = "NO_POOLS_DISCOVERED"
        else:
            self.last_scan_terminal_state = "POOLS_DISCOVERED"

        logger.info(
            "DEX scan terminal_state=%s scanner_errors=%d pools=%d",
            self.last_scan_terminal_state,
            self.scanner_errors,
            len(all_pools),
        )

        return all_pools
'''

new_text, count = pattern.subn(replacement, text, count=1)
if count == 0:
    print("[WARN] Could not replace scan_all_dexes automatically. Manual patch needed.")
else:
    text = new_text
    print("[PATCH] scan_all_dexes now uses on-chain V2 primary discovery")

# Gate DEXScreener fetch function if found.
if "DEXScreener is disabled by default" not in text:
    fetch_pattern = re.compile(
        r'''    async def _fetch_live_pairs_for_token\(self, address: str\) -> List\[Dict\[str, Any\]\]:
        .*?
        return normalized_pairs
''',
        re.DOTALL,
    )
    fetch_replacement = r'''    async def _fetch_live_pairs_for_token(self, address: str) -> List[Dict[str, Any]]:
        """Fetch external pair metadata only when USE_DEXSCREENER=true.

        DEXScreener is disabled by default and is never the execution quote source.
        """
        if address in self._token_pool_cache:
            cached = self._token_pool_cache.get(address)
            return cached if isinstance(cached, list) else []

        if not getattr(self, "use_dexscreener", False):
            self._token_pool_cache[address] = []
            return []

        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._fetch_json(session, f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        except Exception as exc:
            logger.exception("DEXScreener pair fetch failed for token %s: %s", address, exc)
            self._token_pool_cache[address] = []
            return []

        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        if pairs is None:
            pairs = []

        if not isinstance(pairs, list):
            logger.warning("DEXScreener returned malformed pairs for token %s: %s", address, type(pairs).__name__)
            pairs = []

        normalized_pairs = [p for p in pairs if isinstance(p, dict)]
        self._token_pool_cache[address] = normalized_pairs
        return normalized_pairs
'''
    text, fcount = fetch_pattern.subn(fetch_replacement, text, count=1)
    if fcount:
        print("[PATCH] DEXScreener fetch gated behind USE_DEXSCREENER=true")

arb_path.write_text(text, encoding="utf-8", newline="\n")

# Patch .env defaults.
env_path = ROOT / ".env"
if env_path.exists():
    backup(env_path)
    env = env_path.read_text(encoding="utf-8", errors="replace")
else:
    env = ""

def set_env(text: str, key: str, value: str) -> str:
    rx = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if rx.search(text):
        return rx.sub(line, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"

env = set_env(env, "DISCOVERY_SOURCE", "onchain_v2")
env = set_env(env, "USE_DEXSCREENER", "false")
env = set_env(env, "ONCHAIN_DISCOVERY_MAX_TOKENS", "80")
env = set_env(env, "ONCHAIN_DISCOVERY_MAX_PAIRS", "2500")
env = set_env(env, "ONCHAIN_DISCOVERY_CONCURRENCY", "32")
env_path.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env discovery defaults updated")

# Compile changed files.
for target in [adapter_path, arb_path]:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(target)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[FAIL] py_compile {target}")
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)
    print(f"[OK] compiled {target}")

print("[DONE] On-chain V2 discovery adapter installed.")
print("")
print("Next:")
print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
