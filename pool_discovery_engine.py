"""Compatibility entrypoint for Apex-Omega pool discovery.

The production implementation lives in
``apex_omega_core.core.all_pool_discovery``.  This root-level module exists so
operators and scripts can continue to import or compile
``pool_discovery_engine.py`` without depending on markdown-generated artifacts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from apex_omega_core.core.all_pool_discovery import (  # noqa: E402
    DiscoveredPool,
    PoolFamily,
    Rpc,
    decode_address,
    decode_reserves,
    decode_slot0,
    decode_uint,
    discover_all_pools,
    discover_balancer_scaffold,
    discover_curve_scaffold,
    discover_v2,
    discover_v3,
    encode_get_pair,
    encode_get_pool,
    normalize_addr,
    rpc_url,
    save_pool_report,
)


async def discover(tokens: Iterable[Any]) -> list[DiscoveredPool]:
    """Discover supported pools for the provided token objects or addresses."""
    return await discover_all_pools(tokens)


def discover_sync(tokens: Iterable[Any]) -> list[DiscoveredPool]:
    """Synchronous convenience wrapper for scripts and smoke tests."""
    return asyncio.run(discover_all_pools(tokens))


__all__ = [
    "DiscoveredPool",
    "PoolFamily",
    "Rpc",
    "decode_address",
    "decode_reserves",
    "decode_slot0",
    "decode_uint",
    "discover",
    "discover_all_pools",
    "discover_balancer_scaffold",
    "discover_curve_scaffold",
    "discover_sync",
    "discover_v2",
    "discover_v3",
    "encode_get_pair",
    "encode_get_pool",
    "normalize_addr",
    "rpc_url",
    "save_pool_report",
]
