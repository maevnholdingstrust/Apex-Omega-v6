
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from collections import Counter

ROOT = Path.cwd()
PY = ROOT / "python"
sys.path.insert(0, str(PY))


def load_active_env():
    p = ROOT / "runtime" / "active_endpoints.env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


async def main():
    load_active_env()

    from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor
    from apex_omega_core.core.all_pool_discovery import discover_all_pools, save_pool_report

    monitor = PolygonDEXMonitor()
    await monitor.refresh_market_registry()

    tokens = getattr(monitor, "tokens", None) or getattr(monitor, "target_tokens", None) or []
    if not tokens:
        meta = getattr(monitor, "token_metadata", {}) or {}
        tokens = [{"address": addr, **(payload if isinstance(payload, dict) else {})} for addr, payload in meta.items()]

    print("=== APEX ALL-POOLS DISCOVERY AUDIT ===")
    print(f"tokens_loaded={len(tokens)}")
    print(f"families={os.getenv('DISCOVER_POOL_FAMILIES', 'v2,v3,algebra,curve,balancer')}")
    print(f"max_tokens={os.getenv('ALL_POOLS_MAX_TOKENS', os.getenv('ONCHAIN_DISCOVERY_MAX_TOKENS', '100'))}")
    print(f"max_pairs={os.getenv('ALL_POOLS_MAX_PAIRS', os.getenv('ONCHAIN_DISCOVERY_MAX_PAIRS', '5000'))}")
    print("")

    pools = await discover_all_pools(tokens)
    save_pool_report(pools)

    counts = Counter(p.family for p in pools)
    print("=== DISCOVERED POOLS BY FAMILY ===")
    for family, count in sorted(counts.items()):
        print(f"{family}: {count}")

    print("")
    print(f"total_pools={len(pools)}")
    print("[WRITE] runtime/all_discovered_pools.json")
    print("[WRITE] runtime/all_discovered_pool_counts.json")

    if pools:
        print("")
        print("=== SAMPLE ===")
        for p in pools[:25]:
            print(f"{p.family:<16} {p.dex_name:<22} {p.pool_address} fee={p.fee_tier or p.fee_bps} exec={p.execution_supported}")


if __name__ == "__main__":
    asyncio.run(main())
