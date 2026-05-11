# fix_03_scanner_none_guard.ps1
# Adds scanner guard utilities for None-safe pool discovery.

$ErrorActionPreference = "Stop"

Write-Host "=== APEX SCANNER NONE-GUARD FIX ==="

$repo = Get-Location
$coreDir = Join-Path $repo "python\apex_omega_core\core"

if (!(Test-Path $coreDir)) {
    throw "Missing core folder: $coreDir"
}

$guardFile = Join-Path $coreDir "scanner_guards.py"

if (Test-Path $guardFile) {
    $backup = "$guardFile.bak_$(Get-Date -Format yyyyMMdd_HHmmss)"
    Copy-Item $guardFile $backup
    Write-Host "Existing guard file backed up: $backup"
}

$content = @'
"""
APEX-OMEGA scanner guards.

Purpose:
- prevent DEX scanners from returning None into iterable call sites
- preserve error telemetry
- keep execution suppressed when data intake fails
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


def normalize_pool_result(result: Any, dex_name: str = "unknown") -> list:
    """
    Convert scanner output into a safe list.

    Rules:
    - None -> []
    - list -> list
    - tuple/set -> list
    - other iterable -> list
    - non-iterable -> []
    """
    if result is None:
        logger.warning("DEX %s returned None; treating as empty pool list", dex_name)
        return []

    if isinstance(result, list):
        return result

    if isinstance(result, (tuple, set)):
        return list(result)

    if isinstance(result, (str, bytes, dict)):
        logger.warning(
            "DEX %s returned unexpected scanner type %s; treating as empty pool list",
            dex_name,
            type(result).__name__,
        )
        return []

    if isinstance(result, Iterable):
        try:
            return list(result)
        except Exception as exc:
            logger.exception("DEX %s iterable conversion failed: %s", dex_name, exc)
            return []

    logger.warning(
        "DEX %s returned non-iterable scanner type %s; treating as empty pool list",
        dex_name,
        type(result).__name__,
    )
    return []


async def safe_scan_call(scanner_fn, dex_name: str = "unknown", *args, **kwargs) -> list:
    """
    Safely call sync or async scanner functions.

    Always returns list.
    Never returns None.
    """
    try:
        result = scanner_fn(*args, **kwargs)

        if inspect.isawaitable(result):
            result = await result

        return normalize_pool_result(result, dex_name=dex_name)

    except Exception as exc:
        logger.exception("Error scanning %s: %s", dex_name, exc)
        return []


def classify_scan_terminal_state(
    pools: list,
    opportunities: list | None = None,
    scanner_errors: int = 0,
) -> str:
    """
    Distinguish data failure from true no-opportunity state.
    """
    opportunities = opportunities or []

    if scanner_errors > 0 and len(pools) == 0:
        return "REJECTED_BY_DATA_INTAKE_FAILURE"

    if len(pools) == 0:
        return "NO_POOLS_DISCOVERED"

    if len(opportunities) == 0:
        return "NO_EXECUTABLE_OPPORTUNITY"

    return "OPPORTUNITIES_DISCOVERED"
'@

Set-Content -Path $guardFile -Value $content -Encoding UTF8

Write-Host "Created scanner guard module:"
Write-Host "  $guardFile"
Write-Host ""
Write-Host "Next surgical step requires the actual scanner call sites."
Write-Host "Send me the files listed below and I will give you the exact one-click patch."