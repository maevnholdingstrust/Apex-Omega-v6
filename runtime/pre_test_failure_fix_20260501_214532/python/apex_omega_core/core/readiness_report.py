from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass
from typing import Any

from .runtime_config import RuntimeConfig, load_runtime_config


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    production_ready: bool
    components: list[ComponentStatus]
    missing_live_env: list[str]
    chain_id: int
    dry_run: bool
    live_trading_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "production_ready": self.production_ready,
            "components": [asdict(component) for component in self.components],
            "missing_live_env": list(self.missing_live_env),
            "chain_id": self.chain_id,
            "dry_run": self.dry_run,
            "live_trading_enabled": self.live_trading_enabled,
        }


def _module_status(module: str, name: str, detail: str) -> ComponentStatus:
    try:
        importlib.import_module(module)
    except Exception as exc:
        return ComponentStatus(name, False, f"{detail} unavailable: {exc}")
    return ComponentStatus(name, True, detail)


def _rust_status() -> ComponentStatus:
    try:
        rust = importlib.import_module("apex_omega_core_rust")
    except Exception as exc:
        return ComponentStatus("native_math", False, f"Rust/PyO3 extension unavailable: {exc}")

    required = (
        "amm_swap_core",
        "simulate_route_core",
        "optimize_route_core",
        "p_fill_logistic",
        "optimal_tip_gwei",
        "compute_net_edge_v7",
    )
    missing = [name for name in required if getattr(rust, name, None) is None]
    if missing:
        return ComponentStatus("native_math", False, f"missing native functions: {', '.join(missing)}")
    return ComponentStatus("native_math", True, "Rust/PyO3 hot math, route simulation, and gas EV kernels available")


def _redis_status(config: RuntimeConfig) -> ComponentStatus:
    try:
        importlib.import_module("redis.asyncio")
    except Exception as exc:
        return ComponentStatus("redis_cache", False, f"redis package unavailable: {exc}")

    try:
        from .redis_state import RedisState

        state = RedisState()
        enabled = state.enabled()
    except Exception as exc:
        return ComponentStatus("redis_cache", False, f"RedisState unavailable: {exc}")

    if not enabled:
        return ComponentStatus("redis_cache", True, "Redis support installed; REDIS_ENABLED is false so runtime uses in-process cache")
    return ComponentStatus("redis_cache", True, f"Redis support enabled for {config.environment}")


def build_readiness_report(config: RuntimeConfig | None = None) -> ReadinessReport:
    cfg = config or load_runtime_config()
    components = [
        _rust_status(),
        _redis_status(cfg),
        _module_status("apex_omega_core.core.mev_gas_oracle", "ai_ml_execution_model", "p_fill and EIP-1559 EV model importable"),
        _module_status("apex_omega_core.execution.pre_execution_pipeline", "canon_execution_flow", "gate -> C1 -> fork sim -> execute C1 -> reload state -> C2 flow importable"),
        _module_status("apex_omega_core.core.execution_compiler", "payload_compiler", "C1/C2 route envelope compiler importable"),
        _module_status("apex_omega_core.core.contract_invoker", "broadcast_core", "contract invoker and relay path importable"),
    ]
    missing_live_env = cfg.missing_for_live() if cfg.live_trading_enabled and not cfg.dry_run else []
    production_ready = all(component.ok for component in components) and not missing_live_env
    return ReadinessReport(
        production_ready=production_ready,
        components=components,
        missing_live_env=missing_live_env,
        chain_id=cfg.chain_id,
        dry_run=cfg.dry_run,
        live_trading_enabled=cfg.live_trading_enabled,
    )
