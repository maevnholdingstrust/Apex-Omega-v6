
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
