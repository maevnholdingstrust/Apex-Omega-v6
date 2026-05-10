from pathlib import Path
import json, time

runtime = Path("runtime")
runtime.mkdir(exist_ok=True)

out = runtime / "discovery_universe.json"

payload = {
    "generated_at": time.time(),
    "pool_count": 0,
    "token_count": 0,
    "pools": [],
    "tokens": [],
    "status": "EMPTY_BOOTSTRAP_WAITING_FOR_REAL_DISCOVERY"
}

out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"[WRITE] {out}")
print("[OK] Empty discovery universe created. Run bot discovery next to populate real pools.")
