from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
PY = ROOT / "python"

sys.path.insert(0, str(PY))

try:
    from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor
    from apex_omega_core.core.onchain_v2_discovery import discover_v2_pools_onchain
except Exception as exc:
    print("[FAIL] Could not import Apex modules.")
    print(repr(exc))
    raise SystemExit(1)


def load_active_env() -> None:
    active_env = ROOT / "runtime" / "active_endpoints.env"
    if not active_env.exists():
        return

    for raw in active_env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def token_addr(token: Any) -> str | None:
    if isinstance(token, str) and token.startswith("0x") and len(token) == 42:
        return token.lower()

    if isinstance(token, dict):
        for key in ("address", "token_address", "contract_address"):
            value = token.get(key)
            if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
                return value.lower()

    for attr in ("address", "token_address", "contract_address"):
        if hasattr(token, attr):
            value = getattr(token, attr)
            if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
                return value.lower()

    return None


def token_symbol(token: Any, addr: str) -> str:
    if isinstance(token, dict):
        for key in ("symbol", "ticker", "name"):
            value = token.get(key)
            if value:
                return str(value)

    for attr in ("symbol", "ticker", "name"):
        if hasattr(token, attr):
            value = getattr(token, attr)
            if value:
                return str(value)

    return addr[:8]


async def main() -> int:
    load_active_env()

    monitor = PolygonDEXMonitor()
    await monitor.refresh_market_registry()

    tokens = getattr(monitor, "tokens", None) or getattr(monitor, "target_tokens", None) or []
    if not tokens:
        # Fallback: some builds store token metadata differently.
        meta = getattr(monitor, "token_metadata", {}) or {}
        if isinstance(meta, dict):
            tokens = []
            for addr, payload in meta.items():
                if isinstance(payload, dict):
                    row = dict(payload)
                    row.setdefault("address", addr)
                    tokens.append(row)
                else:
                    tokens.append({"address": addr, "symbol": str(payload)})

    token_map: dict[str, dict[str, str]] = {}
    normalized_tokens = []

    for t in tokens:
        addr = token_addr(t)
        if not addr:
            continue
        sym = token_symbol(t, addr)
        token_map[addr] = {"address": addr, "symbol": sym}
        normalized_tokens.append({"address": addr, "symbol": sym})

    factories = getattr(monitor, "_all_dexes", {}) or {}
    if not factories:
        print("[FAIL] No DEX factories found on monitor._all_dexes")
        return 2

    max_tokens = int(os.getenv("VENUE_AUDIT_MAX_TOKENS", str(len(normalized_tokens))))
    max_pairs = int(os.getenv("VENUE_AUDIT_MAX_PAIRS", "20000"))
    concurrency = int(os.getenv("VENUE_AUDIT_CONCURRENCY", "48"))

    print("=== APEX TOKEN VENUE AUDIT ===")
    print(f"tokens_loaded={len(normalized_tokens)}")
    print(f"tokens_scanned={min(max_tokens, len(normalized_tokens))}")
    print(f"factories={len(factories)}")
    print(f"max_pairs={max_pairs}")
    print(f"concurrency={concurrency}")
    print("source=onchain_v2_factory_getPair")
    print("")

    pools = await discover_v2_pools_onchain(
        normalized_tokens,
        factories=factories,
        max_tokens=max_tokens,
        max_pairs=max_pairs,
        concurrency=concurrency,
    )

    venues_by_token: dict[str, set[str]] = defaultdict(set)
    pools_by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for p in pools:
        dex = str(p.dex_name)
        t0 = p.token0.lower()
        t1 = p.token1.lower()

        for addr in (t0, t1):
            venues_by_token[addr].add(dex)
            pools_by_token[addr].append({
                "dex": dex,
                "factory": p.factory,
                "pair": p.pair_address,
                "token0": t0,
                "token1": t1,
                "reserve0_raw": str(p.reserve0),
                "reserve1_raw": str(p.reserve1),
                "block_number": p.block_number,
            })

    rows = []
    for addr, venues in venues_by_token.items():
        if len(venues) >= 3:
            info = token_map.get(addr, {"symbol": addr[:8], "address": addr})
            rows.append({
                "symbol": info["symbol"],
                "address": addr,
                "venue_count": len(venues),
                "venues": ",".join(sorted(venues)),
                "pool_count": len(pools_by_token[addr]),
            })

    rows.sort(key=lambda r: (-int(r["venue_count"]), r["symbol"]))

    out_dir = ROOT / "runtime"
    out_dir.mkdir(exist_ok=True)

    csv_path = out_dir / "tokens_executable_3plus_venues.csv"
    json_path = out_dir / "tokens_executable_3plus_venues.json"
    detail_path = out_dir / "tokens_executable_3plus_venues_detail.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "address", "venue_count", "venues", "pool_count"])
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    detail = {
        "tokens_loaded": len(normalized_tokens),
        "tokens_scanned": min(max_tokens, len(normalized_tokens)),
        "factories": factories,
        "pool_count": len(pools),
        "tokens_3plus_count": len(rows),
        "tokens_3plus": rows,
        "pools_by_token": pools_by_token,
    }
    detail_path.write_text(json.dumps(detail, indent=2, default=str), encoding="utf-8")

    print(f"onchain_pools_discovered={len(pools)}")
    print(f"tokens_on_3plus_venues={len(rows)}")
    print("")

    if not rows:
        print("No tokens found on 3+ active V2 factories in the current scan window.")
        print("This can mean the active factory set is still only 6 DEXs, token-pair cap is too low, or many configured tokens have no live V2 pairs.")
    else:
        print("=== TOKENS ON 3+ VENUES ===")
        for r in rows[:50]:
            print(f"{r['symbol']:<14} venues={r['venue_count']} pools={r['pool_count']} {r['address']} :: {r['venues']}")
        if len(rows) > 50:
            print(f"... and {len(rows) - 50} more")

    print("")
    print(f"[WRITE] {csv_path}")
    print(f"[WRITE] {json_path}")
    print(f"[WRITE] {detail_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
