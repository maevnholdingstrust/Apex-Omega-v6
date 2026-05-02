from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys, re

ROOT = Path.cwd()
CORE = ROOT / "python" / "apex_omega_core" / "core"
ENV = ROOT / ".env"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p):
    if p.exists():
        b = p.with_suffix(p.suffix + f".bak_discovery_universe_{STAMP}")
        shutil.copy2(p, b)
        print(f"[BACKUP] {b}")

def write(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    backup(p)
    p.write_text(s, encoding="utf-8", newline="\n")
    print(f"[WRITE] {p}")

def compile_py(p):
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
    print(f"[OK] compiled {p}")

def set_env(src, key, value):
    rx = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    return rx.sub(line, src) if rx.search(src) else src.rstrip() + "\n" + line + "\n"

write(CORE / "discovery_universe.py", r'''
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Any


class UniverseStatus(str, Enum):
    OBSERVED = "OBSERVED"
    SIM_VALIDATED = "SIM_VALIDATED"
    EXECUTABLE = "EXECUTABLE"
    PROFITABLE = "PROFITABLE"
    COOLDOWN = "COOLDOWN"
    BANNED = "BANNED"


class PoolFamily(str, Enum):
    V2_CPMM = "v2_cpmm"
    V3_CLMM = "v3_clmm"
    ALGEBRA_CLMM = "algebra_clmm"
    CURVE_STABLE = "curve_stable"
    BALANCER_WEIGHTED = "balancer_weighted"
    BALANCER_STABLE = "balancer_stable"
    AGGREGATOR = "aggregator"
    UNKNOWN = "unknown"


@dataclass
class PoolUniverseEntry:
    chain_id: int
    pool_address: str
    dex_name: str
    factory_address: str | None
    token0: str
    token1: str
    pool_family: str
    math_mode: str
    fee_bps: int | None = None
    fee_tier: int | None = None
    tvl_usd: float = 0.0
    volume_24h_usd: float = 0.0
    last_active_block: int | None = None
    execution_supported: bool = False
    quote_engine: str = "unsupported"
    calldata_engine: str = "unsupported"
    status: str = UniverseStatus.OBSERVED.value
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUniverseEntry:
    chain_id: int
    token_address: str
    symbol: str = ""
    decimals: int = 18
    labels: list[str] = field(default_factory=list)
    attached_pools: int = 0
    supported_pool_families: list[str] = field(default_factory=list)
    aggregate_tvl_usd: float = 0.0
    rolling_volume_usd: float = 0.0
    candidate_count: int = 0
    fork_sim_pass_count: int = 0
    c1_strike_count: int = 0
    c2_no_op_count: int = 0
    realized_success_count: int = 0
    simulated_net_usd_total: float = 0.0
    realized_net_usd_total: float | None = None
    avg_prediction_error_bps: float = 0.0
    first_seen_block: int | None = None
    last_seen_block: int | None = None
    status: str = UniverseStatus.OBSERVED.value


class DiscoveryUniverse:
    """Dynamic pool/token universe.

    Token Universe = selection discipline.
    32 lanes = throughput/scheduling.
    C1/C2 remain the only decision authorities.
    """

    def __init__(self, path: str = "runtime/discovery_universe.json"):
        self.path = Path(path)
        self.pools: dict[str, PoolUniverseEntry] = {}
        self.tokens: dict[str, TokenUniverseEntry] = {}

    def add_pool(self, pool: PoolUniverseEntry) -> None:
        key = pool.pool_address.lower()
        self.pools[key] = pool

        for token in (pool.token0, pool.token1):
            tkey = token.lower()
            entry = self.tokens.get(tkey)
            if entry is None:
                entry = TokenUniverseEntry(chain_id=pool.chain_id, token_address=token)
                self.tokens[tkey] = entry

            entry.attached_pools += 1
            if pool.pool_family not in entry.supported_pool_families:
                entry.supported_pool_families.append(pool.pool_family)
            entry.aggregate_tvl_usd += float(pool.tvl_usd or 0.0)
            entry.rolling_volume_usd += float(pool.volume_24h_usd or 0.0)
            if pool.last_active_block is not None:
                entry.last_seen_block = max(entry.last_seen_block or 0, pool.last_active_block)
                entry.first_seen_block = entry.first_seen_block or pool.last_active_block

    def classify_tokens(self) -> None:
        for entry in self.tokens.values():
            labels = set(entry.labels)

            if entry.symbol.upper() in {"USDC", "USDC.E", "USDT", "DAI", "FRAX", "TUSD"}:
                labels.add("stable")
            if entry.aggregate_tvl_usd >= 1_000_000:
                labels.add("deep_liquidity")
            if entry.attached_pools >= 5:
                labels.add("multi_pool")
            if entry.realized_success_count > 0:
                entry.status = UniverseStatus.PROFITABLE.value
            elif entry.fork_sim_pass_count > 0:
                entry.status = UniverseStatus.SIM_VALIDATED.value
            elif entry.aggregate_tvl_usd > 0:
                entry.status = UniverseStatus.OBSERVED.value

            entry.labels = sorted(labels)

    def executable_pools(self) -> list[PoolUniverseEntry]:
        return [
            p for p in self.pools.values()
            if p.execution_supported
            and p.status not in {UniverseStatus.COOLDOWN.value, UniverseStatus.BANNED.value}
            and p.tvl_usd > 0
        ]

    def discovery_pools(self) -> list[PoolUniverseEntry]:
        return [
            p for p in self.pools.values()
            if p.status not in {UniverseStatus.BANNED.value}
        ]

    def save(self) -> None:
        self.classify_tokens()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.time(),
            "pool_count": len(self.pools),
            "token_count": len(self.tokens),
            "pools": [asdict(p) for p in self.pools.values()],
            "tokens": [asdict(t) for t in self.tokens.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str = "runtime/discovery_universe.json") -> "DiscoveryUniverse":
        inst = cls(path)
        p = Path(path)
        if not p.exists():
            return inst
        payload = json.loads(p.read_text(encoding="utf-8"))
        for row in payload.get("pools", []):
            pool = PoolUniverseEntry(**row)
            inst.pools[pool.pool_address.lower()] = pool
        for row in payload.get("tokens", []):
            token = TokenUniverseEntry(**row)
            inst.tokens[token.token_address.lower()] = token
        return inst
''')

write(CORE / "lane_scheduler_32.py", r'''
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LaneRole(str, Enum):
    V2_DISCOVERY = "v2_discovery"
    V3_DISCOVERY = "v3_discovery"
    CURVE_BALANCER_SYNC = "curve_balancer_sync"
    AGGREGATOR_ENRICHMENT = "aggregator_enrichment"
    FORK_SIMULATION = "fork_simulation"
    C2_RECOMPUTE = "c2_recompute"
    DNA_REDIS_LOGGING = "dna_redis_logging"
    HEALTH_FAILSAFE = "health_failsafe"


@dataclass
class LaneAssignment:
    lane_id: int
    role: LaneRole
    description: str
    queue_name: str
    max_inflight: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


def default_32_lane_plan() -> list[LaneAssignment]:
    lanes: list[LaneAssignment] = []

    for i in range(1, 9):
        lanes.append(LaneAssignment(i, LaneRole.V2_DISCOVERY, "V2 CPMM reserves + candidate scan", "q:v2"))

    for i in range(9, 17):
        lanes.append(LaneAssignment(i, LaneRole.V3_DISCOVERY, "V3/Algebra slot0/liquidity/fee-tier sync", "q:v3"))

    for i in range(17, 21):
        lanes.append(LaneAssignment(i, LaneRole.CURVE_BALANCER_SYNC, "Curve/Balancer state sync", "q:specialized"))

    for i in range(21, 25):
        lanes.append(LaneAssignment(i, LaneRole.AGGREGATOR_ENRICHMENT, "Aggregator quote enrichment only", "q:aggregators"))

    for i in range(25, 29):
        lanes.append(LaneAssignment(i, LaneRole.FORK_SIMULATION, "Fork/static simulation and payload validation", "q:fork"))

    lanes.append(LaneAssignment(29, LaneRole.C2_RECOMPUTE, "Post-C1 C2 recompute lane A", "q:c2"))
    lanes.append(LaneAssignment(30, LaneRole.C2_RECOMPUTE, "Post-C1 C2 recompute lane B", "q:c2"))
    lanes.append(LaneAssignment(31, LaneRole.DNA_REDIS_LOGGING, "DNA card, Redis, universe updates", "q:dna"))
    lanes.append(LaneAssignment(32, LaneRole.HEALTH_FAILSAFE, "Endpoint health and kill-switch", "q:health"))

    return lanes


def route_pool_to_lane(pool_family: str, index_hint: int = 0) -> int:
    family = (pool_family or "").lower()

    if family == "v2_cpmm":
        return 1 + (index_hint % 8)
    if family in {"v3_clmm", "algebra_clmm"}:
        return 9 + (index_hint % 8)
    if family in {"curve_stable", "balancer_weighted", "balancer_stable"}:
        return 17 + (index_hint % 4)
    if family == "aggregator":
        return 21 + (index_hint % 4)

    return 32
''')

write(CORE / "historical_universe_builder.py", r'''
from __future__ import annotations

import json
from pathlib import Path
from .discovery_universe import DiscoveryUniverse


DNA_LOG_PATHS = [
    Path("logs/dry_run_cycle_pairs.jsonl"),
    Path("logs/dry_run_dna_cards.jsonl"),
    Path("logs/execution_results.jsonl"),
]


def update_universe_from_dna_logs(universe_path: str = "runtime/discovery_universe.json") -> DiscoveryUniverse:
    universe = DiscoveryUniverse.load(universe_path)

    for path in DNA_LOG_PATHS:
        if not path.exists():
            continue

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue

            try:
                row = json.loads(line)
            except Exception:
                continue

            tokens = row.get("tokens") or row.get("route_tokens") or []
            if not isinstance(tokens, list):
                continue

            simulated_net = float(row.get("simulated_net_usd") or row.get("net_profit_usd") or 0.0)
            realized_net = row.get("realized_net_opportunity_usd")

            for token in tokens:
                if not isinstance(token, str) or not token.startswith("0x"):
                    continue
                entry = universe.tokens.get(token.lower())
                if not entry:
                    continue

                entry.candidate_count += 1
                entry.simulated_net_usd_total += simulated_net

                status = str(row.get("realized_status") or "")
                if status == "LIVE_REALIZED":
                    entry.realized_success_count += 1
                    if realized_net is not None:
                        entry.realized_net_usd_total = (entry.realized_net_usd_total or 0.0) + float(realized_net)

                if bool(row.get("fork_sim_pass")):
                    entry.fork_sim_pass_count += 1

                if str(row.get("c1_decision") or "").upper() in {"BUILD_PAYLOAD", "STRIKE", "EXECUTE"}:
                    entry.c1_strike_count += 1

                if str(row.get("c2_decision") or "").upper() == "NO_OP":
                    entry.c2_no_op_count += 1

    universe.save()
    return universe
''')

write(CORE / "discovery_policy.py", r'''
from __future__ import annotations

DISCOVERY_CANON = """
Token Universe = what the system is allowed to observe and prioritize.
32 lanes = how the system parallelizes work.

The token universe is not a decision authority.
The pool universe is not a decision authority.
C1/C2 remain the only decision authorities.

Discovery can observe:
- V2 CPMM
- V3/Algebra
- Curve
- Balancer
- aggregators as enrichment only

Execution can consume only:
- math-supported
- fee-tier-known
- reserve/state-verified
- fork-sim-passed
- C1/C2-approved candidates.
"""
''')

# Safe .env defaults
env = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
for k, v in {
    "DISCOVERY_UNIVERSE_ENABLED": "true",
    "DISCOVERY_UNIVERSE_PATH": "runtime/discovery_universe.json",
    "LANE_SCHEDULER_32_ENABLED": "true",
    "TOKEN_UNIVERSE_MODE": "dynamic_historical",
    "POOL_UNIVERSE_FIRST": "true",
    "EXECUTION_ENABLED": "false",
    "USE_DEXSCREENER": "false",
    "DISCOVERY_SOURCE": "onchain_v2",
}.items():
    env = set_env(env, k, v)

backup(ENV)
ENV.write_text(env, encoding="utf-8", newline="\n")
print("[PATCH] .env discovery universe defaults updated")

for p in [
    CORE / "discovery_universe.py",
    CORE / "lane_scheduler_32.py",
    CORE / "historical_universe_builder.py",
    CORE / "discovery_policy.py",
]:
    compile_py(p)

print("[DONE] Discovery universe + 32-lane scheduler installed.")
print("Next boot:")
print("  powershell -ExecutionPolicy Bypass -File .\\boot_with_latency_monitor.ps1")
