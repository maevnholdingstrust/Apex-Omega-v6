from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    cwd = Path.cwd()
    candidates = [
        cwd / ".env",
        cwd / "python" / "apex_omega_core" / ".env",
        Path(__file__).resolve().parents[3] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ValueError(f"Invalid boolean env var {name}={value!r}")


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class RuntimeConfig:
    chain_id: int
    environment: str
    live_trading_enabled: bool
    dry_run: bool
    polygon_rpc: str
    polygon_wss: str
    executor_private_key: str
    bundle_signer_private_key: str
    c1_executor_address: str
    c2_executor_address: str
    aave_v3_pool_address: str
    balancer_vault_address: str
    titan_mev_us_west: str
    flashbots_relay: str
    fastlane_relay: str
    marlin_relay: str
    min_net_profit_usd: float
    min_raw_spread_bps: float
    max_route_slippage_bps: float
    max_mempool_degradation_bps: float
    min_pool_tvl_usd: float
    max_trade_to_pool_ratio_bps: float
    risk_buffer_usd: float
    c1_gas_usd: float
    c2_gas_usd: float
    flash_loan_fee_bps: float
    bundle_target_block_offset: int
    bundle_max_block_window: int

    @property
    def primary_rpc(self) -> str:
        return self.polygon_rpc

    @property
    def relays(self) -> dict[str, str]:
        return {
            k: v for k, v in {
                "titan_mev_us_west": self.titan_mev_us_west,
                "flashbots": self.flashbots_relay,
                "fastlane": self.fastlane_relay,
                "marlin": self.marlin_relay,
            }.items() if v
        }

    def missing_for_live(self) -> list[str]:
        required = {
            "POLYGON_RPC": self.polygon_rpc,
            "EXECUTOR_PRIVATE_KEY": self.executor_private_key,
            "C1_INSTITUTIONAL_EXECUTOR_ADDRESS": self.c1_executor_address,
            "C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS": self.c2_executor_address,
            "AAVE_V3_POOL_ADDRESS": self.aave_v3_pool_address,
        }
        return [name for name, value in required.items() if not value]

    def assert_safe_to_send(self) -> None:
        if not self.live_trading_enabled or self.dry_run:
            raise RuntimeError("Live transaction sending disabled: set LIVE_TRADING_ENABLED=true and DRY_RUN=false")
        missing = self.missing_for_live()
        if missing:
            raise RuntimeError(f"Missing required live execution env vars: {', '.join(missing)}")


def load_runtime_config() -> RuntimeConfig:
    _load_dotenv_if_available()
    rpc = os.getenv("POLYGON_RPC") or os.getenv("POLYGON_HTTP") or os.getenv("ALCHEMY_HTTP_1") or ""
    wss = os.getenv("POLYGON_WSS") or os.getenv("ALCHEMY_WSS_1") or ""
    return RuntimeConfig(
        chain_id=_get_int("CHAIN_ID", 137),
        environment=os.getenv("ENVIRONMENT", "development"),
        live_trading_enabled=_get_bool("LIVE_TRADING_ENABLED", False),
        dry_run=_get_bool("DRY_RUN", True),
        polygon_rpc=rpc,
        polygon_wss=wss,
        executor_private_key=os.getenv("EXECUTOR_PRIVATE_KEY") or os.getenv("PRIVATE_KEY", ""),
        bundle_signer_private_key=os.getenv("BUNDLE_SIGNER_PRIVATE_KEY", ""),
        c1_executor_address=os.getenv("C1_INSTITUTIONAL_EXECUTOR_ADDRESS") or os.getenv("C1_TARGET", ""),
        c2_executor_address=os.getenv("C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS") or os.getenv("C2_TARGET", ""),
        aave_v3_pool_address=os.getenv("AAVE_V3_POOL_ADDRESS") or os.getenv("AAVE_POOL_ADDRESS", ""),
        balancer_vault_address=os.getenv("BALANCER_VAULT_ADDRESS") or os.getenv("BALANCER_VAULT", ""),
        titan_mev_us_west=os.getenv("TITAN_MEV_US_WEST", ""),
        flashbots_relay=os.getenv("FLASHBOTS_RELAY", ""),
        fastlane_relay=os.getenv("FASTLANE_RELAY", ""),
        marlin_relay=os.getenv("MARLIN_RELAY", ""),
        min_net_profit_usd=_get_float("MIN_NET_PROFIT_USD", 1.0),
        min_raw_spread_bps=_get_float("MIN_RAW_SPREAD_BPS", 1.0),
        max_route_slippage_bps=_get_float("MAX_ROUTE_SLIPPAGE_BPS", 100.0),
        max_mempool_degradation_bps=_get_float("MAX_MEMPOOL_DEGRADATION_BPS", 200.0),
        min_pool_tvl_usd=_get_float("MIN_POOL_TVL_USD", 10_000.0),
        max_trade_to_pool_ratio_bps=_get_float("MAX_TRADE_TO_POOL_RATIO_BPS", 500.0),
        risk_buffer_usd=_get_float("RISK_BUFFER_USD", 0.0),
        c1_gas_usd=_get_float("C1_GAS_USD", 0.38),
        c2_gas_usd=_get_float("C2_GAS_USD", 0.55),
        flash_loan_fee_bps=_get_float(
            "FLASH_LOAN_FEE_BPS",
            _get_float("FLASH_FEE_BPS", 9.0),
        ),
        bundle_target_block_offset=_get_int("BUNDLE_TARGET_BLOCK_OFFSET", 1),
        bundle_max_block_window=_get_int("BUNDLE_MAX_BLOCK_WINDOW", 5),
    )
