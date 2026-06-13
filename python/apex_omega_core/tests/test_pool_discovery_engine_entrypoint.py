from __future__ import annotations

import importlib
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / "pool_discovery_engine.py"


def test_pool_discovery_engine_is_valid_python() -> None:
    py_compile.compile(str(ENTRYPOINT), doraise=True)


def test_pool_discovery_engine_has_no_markdown_fence_artifacts() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "**File:**" not in text
    assert "```" not in text


def test_pool_discovery_engine_exports_production_api() -> None:
    module = importlib.import_module("pool_discovery_engine")
    assert callable(module.discover_all_pools)
    assert callable(module.discover_sync)
    assert module.DiscoveredPool.__name__ == "DiscoveredPool"
