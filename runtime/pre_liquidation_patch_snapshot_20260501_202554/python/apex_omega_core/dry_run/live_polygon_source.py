from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

from apex_omega_core.core.polygon_arbitrage import (
    POLYGON_CANONICAL_TOKEN_METADATA,
    PolygonDEXMonitor,
)


class LiveDryRunDataError(RuntimeError):
    pass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LiveDryRunDataError(f"INVALID_FLOAT_ENV: {name}={raw!r}") from exc


def _load_root_env() -> None:
    for env_path in (Path.cwd() / ".env", Path.cwd() / "runtime" / "active_endpoints.env"):
        if not env_path.exists():
            continue
        loaded: dict[str, str] = {}
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            for env_key, env_value in {**loaded, **os.environ}.items():
                value = value.replace("${" + env_key + "}", env_value)
            loaded[key] = value
            os.environ.setdefault(key, value)

    rpc = os.getenv("ACTIVE_EXECUTION_RPC") or os.getenv("POLYGON_RPC_URL") or os.getenv("POLYGON_RPC")
    if rpc:
        os.environ.setdefault("POLYGON_RPC_URL", rpc)
        os.environ.setdefault("ACTIVE_EXECUTION_RPC", rpc)
        os.environ.setdefault("WEB3_PROVIDER_URI", rpc)
        os.environ.setdefault("PRIVATE_RPC_URL", rpc)


def _rpc_url() -> str:
    _load_root_env()
    return (
        os.getenv("ACTIVE_EXECUTION_RPC")
        or os.getenv("POLYGON_RPC_URL")
        or os.getenv("POLYGON_RPC")
        or os.getenv("WEB3_PROVIDER_URI")
        or os.getenv("PRIVATE_RPC_URL")
        or ""
    )


def _token_universe() -> list[dict[str, str]]:
    wanted = {
        s.strip().upper()
        for s in os.getenv(
            "DRY_RUN_LIVE_SYMBOLS",
            "USDC,USDC.E,USDT,DAI,FRAX,WPOL,POL,WETH,WBTC,LINK,CRV,QUICK",
        ).split(",")
        if s.strip()
    }
    tokens: list[dict[str, str]] = []
    for address, meta in POLYGON_CANONICAL_TOKEN_METADATA.items():
        symbol = str(meta.get("symbol", "")).upper()
        if symbol in wanted:
            tokens.append({"address": address, "symbol": symbol})
    return tokens


def _addr_key(address: str) -> str:
    return str(address or "").lower()


def _decimals(monitor: PolygonDEXMonitor, address: str) -> int:
    return monitor._token_decimals_for_tvl(address)


def _price(monitor: PolygonDEXMonitor, address: str) -> float | None:
    return monitor._token_usd_price_for_tvl(address)


def _human(raw: float, decimals: int) -> float:
    return float(raw or 0.0) / float(10 ** decimals)


def _pool_price_for_token(monitor: PolygonDEXMonitor, pool: Any, token: str) -> float | None:
    token_key = _addr_key(token)
    token0 = _addr_key(getattr(pool, "token0", ""))
    token1 = _addr_key(getattr(pool, "token1", ""))
    reserve0 = float(getattr(pool, "reserve0", 0.0) or 0.0)
    reserve1 = float(getattr(pool, "reserve1", 0.0) or 0.0)
    if reserve0 <= 0.0 or reserve1 <= 0.0:
        return None

    if token_key == token0:
        p1 = _price(monitor, token1)
        if p1 is None:
            return None
        amount0 = _human(reserve0, _decimals(monitor, token0))
        amount1 = _human(reserve1, _decimals(monitor, token1))
        return (amount1 * p1 / amount0) if amount0 > 0.0 else None

    if token_key == token1:
        p0 = _price(monitor, token0)
        if p0 is None:
            return None
        amount0 = _human(reserve0, _decimals(monitor, token0))
        amount1 = _human(reserve1, _decimals(monitor, token1))
        return (amount0 * p0 / amount1) if amount1 > 0.0 else None

    return None


async def collect_live_polygon_candidates(limit: int) -> list[dict[str, Any]]:
    rpc = _rpc_url()
    if not rpc:
        raise LiveDryRunDataError("LIVE_RPC_REQUIRED: no Polygon RPC URL configured")

    monitor = PolygonDEXMonitor(web3_provider=rpc)
    tokens = _token_universe()
    if len(tokens) < 2:
        raise LiveDryRunDataError("LIVE_TOKEN_UNIVERSE_EMPTY")

    pools = await monitor.scan_all_dexes(tokens)
    executable_pools = [
        p
        for p in pools
        if bool(getattr(p, "execution_supported", True))
        and str(getattr(p, "pool_type", "v2")).lower() in {"v2", "v2_cpmm"}
        and bool(getattr(p, "tvl_verified", False))
        and int(getattr(p, "block_number", 0) or 0) > 0
        and float(getattr(p, "tvl_usd", 0.0) or 0.0) >= float(os.getenv("DRY_RUN_LIVE_MIN_POOL_TVL_USD", "10000"))
        and float(getattr(p, "reserve0", 0.0) or 0.0) > 0.0
        and float(getattr(p, "reserve1", 0.0) or 0.0) > 0.0
    ]
    if not executable_pools:
        raise LiveDryRunDataError("NO_LIVE_EXECUTABLE_POOLS")

    quotes: dict[str, list[tuple[Any, float]]] = {}
    for token in tokens:
        address = token["address"]
        for pool in executable_pools:
            if _addr_key(getattr(pool, "token0", "")) != _addr_key(address) and _addr_key(
                getattr(pool, "token1", "")
            ) != _addr_key(address):
                continue
            price = _pool_price_for_token(monitor, pool, address)
            if price is not None and price > 0.0:
                quotes.setdefault(_addr_key(address), []).append((pool, price))

    candidates: list[dict[str, Any]] = []
    trade_size = _env_float("DRY_RUN_LIVE_TRADE_SIZE_USD", 10000.0)
    gas_cost = _env_float("DRY_RUN_LIVE_GAS_COST_USD", 0.25)
    min_c1_profit = _env_float("DRY_RUN_LIVE_MIN_C1_PROFIT_USD", 2.0)
    min_spread_bps = _env_float("DRY_RUN_LIVE_MIN_SPREAD_BPS", 0.01)
    max_spread_bps = _env_float("DRY_RUN_LIVE_MAX_SPREAD_BPS", 500.0)
    max_pool_usage = _env_float("DRY_RUN_LIVE_MAX_POOL_USAGE", 0.02)

    for token in tokens:
        token_quotes = quotes.get(_addr_key(token["address"]), [])
        if len(token_quotes) < 2:
            continue
        for buy_pool, buy_price in sorted(token_quotes, key=lambda x: x[1]):
            for sell_pool, sell_price in sorted(token_quotes, key=lambda x: x[1], reverse=True):
                if getattr(buy_pool, "address", "") == getattr(sell_pool, "address", ""):
                    continue
                spread_bps = ((sell_price - buy_price) / buy_price) * 10_000.0
                if spread_bps <= min_spread_bps or spread_bps > max_spread_bps:
                    continue
                weakest_tvl = min(
                    float(getattr(buy_pool, "tvl_usd", 0.0) or 0.0),
                    float(getattr(sell_pool, "tvl_usd", 0.0) or 0.0),
                )
                if weakest_tvl <= 0.0:
                    continue
                sized_trade = min(trade_size, weakest_tvl * max_pool_usage)
                if sized_trade <= 0.0:
                    continue
                gross = sized_trade * (spread_bps / 10_000.0)
                c1_net = gross - gas_cost
                c2_net = max(0.0, c1_net * 0.20 - gas_cost)
                if c1_net < min_c1_profit:
                    continue
                candidates.append(
                    {
                        "live_data": True,
                        "source": "polygon_onchain_v2_reserves",
                        "block_number": int(getattr(buy_pool, "block_number", 0) or getattr(sell_pool, "block_number", 0) or 0),
                        "token": token["symbol"],
                        "token_address": token["address"],
                        "buy_pool": getattr(buy_pool, "address", ""),
                        "sell_pool": getattr(sell_pool, "address", ""),
                        "buy_dex": getattr(buy_pool, "dex", ""),
                        "sell_dex": getattr(sell_pool, "dex", ""),
                        "buy_price_usd": buy_price,
                        "sell_price_usd": sell_price,
                        "spread_bps": spread_bps,
                        "trade_size_usd": sized_trade,
                        "weakest_pool_tvl_usd": weakest_tvl,
                        "pool_usage_fraction": sized_trade / weakest_tvl,
                        "min_c1_profit_usd": min_c1_profit,
                        "estimated_profit_usd": c1_net,
                        "post_c1_estimated_profit_usd": c2_net,
                    }
                )

    candidates.sort(key=lambda c: c["estimated_profit_usd"], reverse=True)
    if len(candidates) < limit:
        raise LiveDryRunDataError(
            f"LIVE_DATA_INSUFFICIENT_CANDIDATES: required={limit} available={len(candidates)}"
        )
    return candidates[:limit]


def build_live_dry_run_components(
    limit: int,
) -> tuple[Callable[[], list[dict[str, Any]]], Callable[[dict[str, Any]], dict], Callable[[dict[str, Any]], dict]]:
    candidates = asyncio.run(collect_live_polygon_candidates(limit))

    def scanner_fn() -> list[dict[str, Any]]:
        return candidates

    def c1_fn(candidate: dict[str, Any]) -> dict:
        if not candidate.get("live_data"):
            return {"accepted": False, "reason": "NON_LIVE_CANDIDATE_REJECTED"}
        net = float(candidate.get("estimated_profit_usd", 0.0) or 0.0)
        min_profit = _env_float("DRY_RUN_LIVE_MIN_C1_PROFIT_USD", 2.0)
        accepted = net >= min_profit
        return {
            "accepted": accepted,
            "reason": "LIVE_C1_MIN_PROFIT_PASS" if accepted else "LIVE_C1_MIN_PROFIT_FAIL",
            "simulated_net_usd": net,
            "min_profit_usd": min_profit,
            "payload_built": accepted,
        }

    def c2_fn(candidate: dict[str, Any]) -> dict:
        net = float(candidate.get("post_c1_estimated_profit_usd", 0.0) or 0.0)
        min_profit = _env_float("DRY_RUN_LIVE_MIN_C2_PROFIT_USD", 2.0)
        execute = net >= min_profit
        return {
            "action": "EXECUTE" if execute else "NO_OP",
            "simulated_net_usd": net,
            "min_profit_usd": min_profit,
            "reason": "LIVE_C2_MIN_PROFIT_PASS" if execute else "LIVE_C2_MIN_PROFIT_FAIL",
            "state_basis": "post_c1_live_reserve_state",
        }

    return scanner_fn, c1_fn, c2_fn
