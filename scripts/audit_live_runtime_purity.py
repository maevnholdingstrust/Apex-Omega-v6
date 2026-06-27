#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILES = [
    ROOT / "python" / "dry_run.py",
    ROOT / "scripts" / "autonomous_live_scanner.py",
    ROOT / "scripts" / "prove_route_level_forks.py",
    ROOT / "python" / "apex_omega_core" / "core" / "expanded_strategy_steps.py",
    ROOT / "python" / "apex_omega_core" / "core" / "execution_engine.py",
    ROOT / "python" / "apex_omega_core" / "core" / "mev_gas_oracle.py",
    ROOT / "python" / "apex_omega_core" / "core" / "rpc_rotation.py",
    ROOT / "python" / "apex_omega_core" / "core" / "live_data_feeds.py",
    ROOT / "python" / "apex_omega_core" / "core" / "execution_transport_selector.py",
]

FORBIDDEN_PATTERNS = [
    re.compile(r"_simulate_pools\s*\(", re.IGNORECASE),
    re.compile(r"mock_opp", re.IGNORECASE),
    re.compile(r"0xSIM_", re.IGNORECASE),
    re.compile(r"own-capital", re.IGNORECASE),
    re.compile(r"Using static fallback", re.IGNORECASE),
    re.compile(r"so the dashboard always has data", re.IGNORECASE),
]

ALLOWED_SNIPPETS = {
    "python\\dry_run.py": [
        "def _simulate_pools",
        "SYNTHETIC_POOL_DATA_DISABLED",
        "_SIM_TEMPLATES",
        "0xSIM_",
    ],
    "python\\apex_omega_core\\core\\mev_gas_oracle.py": [
        "APEX_ALLOW_STATIC_PRICE_FALLBACK",
        "static gas-token prices are disabled",
        "explicitly enabled static",
    ],
    "python\\apex_omega_core\\core\\live_data_feeds.py": [
        "APEX_ALLOW_STALE_FEED_FALLBACK",
    ],
}


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("/", "\\")


def main() -> int:
    violations: list[dict[str, str]] = []
    for path in RUNTIME_FILES:
        text = path.read_text(encoding="utf-8")
        rel = _relative(path)
        allowed = ALLOWED_SNIPPETS.get(rel, [])
        for pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end < 0:
                    line_end = len(text)
                line = text[line_start:line_end].strip()
                if any(snippet in line for snippet in allowed):
                    continue
                violations.append(
                    {
                        "file": rel,
                        "pattern": pattern.pattern,
                        "line": line,
                    }
                )
    payload = {
        "status": "PASS" if not violations else "FAIL",
        "checked_files": [_relative(path) for path in RUNTIME_FILES],
        "violations": violations,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
