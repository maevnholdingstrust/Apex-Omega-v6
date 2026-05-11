
from __future__ import annotations

import json
from pathlib import Path
from .protocol_execution_gates import gate_protocol_candidate
from .dna_protocol_labels import build_protocol_dna_label

def run_protocol_shadow_check(input_path: str = "runtime/discovery_universe.json", output_path: str = "runtime/protocol_shadow_report.json") -> dict:
    src = Path(input_path)
    if not src.exists():
        payload = {"ok": False, "reason": "missing_discovery_universe", "rows": []}
        Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    data = json.loads(src.read_text(encoding="utf-8"))
    rows = []

    for pool in data.get("pools", []):
        gate = gate_protocol_candidate(pool)
        rows.append({
            "pool": pool.get("pool_address"),
            "gate_ok": gate.ok,
            "gate_reason": gate.reason,
            "dna": build_protocol_dna_label(pool),
        })

    payload = {
        "ok": True,
        "pool_count": len(rows),
        "executable_count": sum(1 for r in rows if r["gate_ok"]),
        "blocked_count": sum(1 for r in rows if not r["gate_ok"]),
        "rows": rows,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

if __name__ == "__main__":
    result = run_protocol_shadow_check()
    print(json.dumps({k:v for k,v in result.items() if k != "rows"}, indent=2))
