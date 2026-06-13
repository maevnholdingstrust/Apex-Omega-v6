from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

from web3 import Web3

from apex_omega_core.core.polygon_arbitrage import (
    POLYGON_CANONICAL_TOKEN_METADATA,
    PolygonDEXMonitor,
)


class LiveDryRunDataError(RuntimeError):
    pass


ERC20_BALANCE_OF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

AAVE_V3_POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "configuration", "type": "uint256"},
                    {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
                    {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
                    {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
                    {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"},
                    {"internalType": "uint128", "name": "currentStableBorrowRate", "type": "uint128"},
                    {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"},
                    {"internalType": "uint16", "name": "id", "type": "uint16"},
                    {"internalType": "address", "name": "aTokenAddress", "type": "address"},
                    {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
                    {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
                    {"internalType": "address", "name": "interestRateStrategyAddress", "type": "address"},
                    {"internalType": "uint128", "name": "accruedToTreasury", "type": "uint128"},
                    {"internalType": "uint128", "name": "unbacked", "type": "uint128"},
                    {"internalType": "uint128", "name": "isolationModeTotalDebt", "type": "uint128"},
                ],
                "internalType": "struct DataTypes.ReserveDataLegacy",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

POLYGON_AAVE_V3_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
POLYGON_BALANCER_V2_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
POLYGON_BALANCER_V3_VAULT = "0xbA1333333333a1BA1108E8412f11850A5C319bA9"
DEFAULT_FLASHLOAN_ASSET = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"


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


def _env_size_fractions() -> list[float]:
    raw = os.getenv("DRY_RUN_LIVE_SIZE_FRACTIONS", "0.10")
    fractions: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError as exc:
            raise LiveDryRunDataError(f"INVALID_SIZE_FRACTION: {item!r}") from exc
        if value <= 0.0 or value > 1.0:
            raise LiveDryRunDataError(f"INVALID_SIZE_FRACTION_RANGE: {item!r}")
        fractions.append(value)
    if not fractions:
        raise LiveDryRunDataError("DRY_RUN_LIVE_SIZE_FRACTIONS_EMPTY")
    return sorted(set(fractions))


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


def _flashloan_asset() -> dict[str, Any]:
    asset = os.getenv("DRY_RUN_FLASHLOAN_ASSET") or os.getenv("FLASHLOAN_ASSET") or DEFAULT_FLASHLOAN_ASSET
    key = _addr_key(asset)
    meta = POLYGON_CANONICAL_TOKEN_METADATA.get(key)
    if not meta:
        raise LiveDryRunDataError(f"UNSUPPORTED_FLASHLOAN_ASSET: {asset}")
    return {"address": key, **meta}


def _erc20_balance(w3: Web3, token: str, holder: str, decimals: int) -> float | None:
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token),
            abi=ERC20_BALANCE_OF_ABI,
        )
        raw = contract.functions.balanceOf(Web3.to_checksum_address(holder)).call()
        return _human(float(raw), decimals)
    except Exception:
        return None


def _aave_v3_liquidity_snapshot(
    w3: Web3,
    token: str,
    pool_address: str,
    decimals: int,
    price: float,
) -> dict[str, Any]:
    try:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=AAVE_V3_POOL_ABI,
        )
        reserve_data = pool.functions.getReserveData(Web3.to_checksum_address(token)).call()
        configuration = int(reserve_data[0])
        a_token_address = str(reserve_data[8])
        flashloan_enabled = bool((configuration >> 63) & 1)
        balance = _erc20_balance(w3, token, a_token_address, decimals)
        return {
            "address": pool_address,
            "method": "aave_v3_getReserveData_aToken_balance",
            "liquidity_holder": a_token_address,
            "flashloan_enabled": flashloan_enabled,
            "available_token": balance,
            "available_usd": None if balance is None else balance * price,
            "available": balance is not None and balance > 0.0 and flashloan_enabled,
        }
    except Exception as exc:
        direct_balance = _erc20_balance(w3, token, pool_address, decimals)
        return {
            "address": pool_address,
            "method": "aave_v3_getReserveData_failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "direct_pool_balance_token": direct_balance,
            "direct_pool_balance_usd": None if direct_balance is None else direct_balance * price,
            "available_token": None,
            "available_usd": None,
            "available": False,
        }


def _vault_liquidity_snapshot(
    w3: Web3,
    token: str,
    vault_address: str,
    decimals: int,
    price: float,
) -> dict[str, Any]:
    balance = _erc20_balance(w3, token, vault_address, decimals)
    return {
        "address": vault_address,
        "method": "erc20_balanceOf_vault",
        "available_token": balance,
        "available_usd": None if balance is None else balance * price,
        "available": balance is not None and balance > 0.0,
    }


def _provider_liquidity_snapshot(rpc: str, asset: dict[str, Any]) -> dict[str, Any]:
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
    if not w3.is_connected():
        return {
            "asset_symbol": asset["symbol"],
            "asset_address": asset["address"],
            "healthy": False,
            "reason": "RPC_NOT_CONNECTED",
            "providers": {},
        }

    token = asset["address"]
    decimals = int(asset["decimals"])
    price = float(asset.get("price_usd", 1.0) or 1.0)
    aave_pool = os.getenv("AAVE_V3_POOL_ADDRESS") or os.getenv("AAVE_POOL_ADDRESS") or POLYGON_AAVE_V3_POOL
    balancer_v2 = os.getenv("BALANCER_VAULT_ADDRESS") or os.getenv("BALANCER_VAULT") or POLYGON_BALANCER_V2_VAULT
    balancer_v3 = os.getenv("BALANCER_VAULT_V3") or os.getenv("BALANCER_V3_VAULT") or POLYGON_BALANCER_V3_VAULT
    out: dict[str, Any] = {
        "aave_v3": _aave_v3_liquidity_snapshot(w3, token, aave_pool, decimals, price),
        "balancer_v2": _vault_liquidity_snapshot(w3, token, balancer_v2, decimals, price),
        "balancer_v3": _vault_liquidity_snapshot(w3, token, balancer_v3, decimals, price),
    }

    available_values = [
        float(p["available_usd"])
        for p in out.values()
        if p.get("available_usd") is not None
    ]
    return {
        "asset_symbol": asset["symbol"],
        "asset_address": asset["address"],
        "asset_price_usd": price,
        "healthy": any(v > 0.0 for v in available_values),
        "max_available_usd": max(available_values) if available_values else 0.0,
        "providers": out,
    }


def _pool_fee_fraction(pool: Any) -> float:
    fee = getattr(pool, "fee", None)
    if fee is not None:
        value = float(fee or 0.0)
        if 0.0 <= value < 1.0:
            return value

    fee_bps = getattr(pool, "fee_bps", None)
    if fee_bps is not None:
        return max(0.0, float(fee_bps or 0.0) / 10_000.0)

    return 0.003


def _pool_amounts_human(monitor: PolygonDEXMonitor, pool: Any) -> tuple[str, str, float, float]:
    token0 = _addr_key(getattr(pool, "token0", ""))
    token1 = _addr_key(getattr(pool, "token1", ""))
    reserve0 = _human(float(getattr(pool, "reserve0", 0.0) or 0.0), _decimals(monitor, token0))
    reserve1 = _human(float(getattr(pool, "reserve1", 0.0) or 0.0), _decimals(monitor, token1))
    return token0, token1, reserve0, reserve1


def _quote_v2_exact_in_human(pool: Any, amount_in: float, reserve_in: float, reserve_out: float) -> float:
    if amount_in <= 0.0 or reserve_in <= 0.0 or reserve_out <= 0.0:
        return 0.0
    amount_in_after_fee = amount_in * max(0.0, 1.0 - _pool_fee_fraction(pool))
    return (amount_in_after_fee * reserve_out) / (reserve_in + amount_in_after_fee)


def _simulate_two_leg_v2_usd(
    monitor: PolygonDEXMonitor,
    buy_pool: Any,
    sell_pool: Any,
    token: str,
    amount_usd: float,
) -> float | None:
    token_key = _addr_key(token)

    buy_token0, buy_token1, buy_reserve0, buy_reserve1 = _pool_amounts_human(monitor, buy_pool)
    if token_key == buy_token0:
        quote_token = buy_token1
        quote_price = _price(monitor, quote_token)
        if not quote_price or quote_price <= 0.0:
            return None
        quote_in = amount_usd / quote_price
        target_amount = _quote_v2_exact_in_human(buy_pool, quote_in, buy_reserve1, buy_reserve0)
    elif token_key == buy_token1:
        quote_token = buy_token0
        quote_price = _price(monitor, quote_token)
        if not quote_price or quote_price <= 0.0:
            return None
        quote_in = amount_usd / quote_price
        target_amount = _quote_v2_exact_in_human(buy_pool, quote_in, buy_reserve0, buy_reserve1)
    else:
        return None

    sell_token0, sell_token1, sell_reserve0, sell_reserve1 = _pool_amounts_human(monitor, sell_pool)
    if token_key == sell_token0:
        quote_token = sell_token1
        quote_price = _price(monitor, quote_token)
        if not quote_price or quote_price <= 0.0:
            return None
        quote_out = _quote_v2_exact_in_human(sell_pool, target_amount, sell_reserve0, sell_reserve1)
    elif token_key == sell_token1:
        quote_token = sell_token0
        quote_price = _price(monitor, quote_token)
        if not quote_price or quote_price <= 0.0:
            return None
        quote_out = _quote_v2_exact_in_human(sell_pool, target_amount, sell_reserve1, sell_reserve0)
    else:
        return None

    return quote_out * quote_price


def _evaluate_size_curve(
    *,
    spread_bps: float,
    weakest_tvl: float,
    max_trade_size: float,
    gas_cost: float,
    fractions: list[float],
    provider_liquidity: dict[str, Any],
    quote_out_usd: Callable[[float], float | None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    best: dict[str, Any] | None = None
    curve: list[dict[str, Any]] = []
    provider_healthy = bool(provider_liquidity.get("healthy", False))
    max_provider_usd = float(provider_liquidity.get("max_available_usd", 0.0) or 0.0)
    for fraction in fractions:
        raw_size = weakest_tvl * fraction
        sized_trade = min(max_trade_size, raw_size)
        provider_ok = provider_healthy and sized_trade <= max_provider_usd
        final_usd = quote_out_usd(sized_trade)
        gross = 0.0 if final_usd is None else final_usd - sized_trade
        c1_net = gross - gas_cost
        c2_eval = max(0.0, c1_net * 0.20 - gas_cost)
        total_eval = c1_net + c2_eval
        row = {
            "pool_usage_fraction": fraction,
            "loan_size_usd": sized_trade,
            "capped_by_trade_size": sized_trade < raw_size,
            "provider_liquidity_ok": provider_ok,
            "quote_out_usd": final_usd,
            "gross_usd": gross,
            "c1_net_usd": c1_net,
            "c2_evaluated_net_usd": c2_eval,
            "total_evaluated_net_usd": total_eval,
        }
        curve.append(row)
        if provider_ok and (best is None or total_eval > best["total_evaluated_net_usd"]):
            best = row

    if best is None:
        best = max(curve, key=lambda r: r["total_evaluated_net_usd"])
    return best, curve


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
    flashloan_asset = _flashloan_asset()
    provider_liquidity = _provider_liquidity_snapshot(rpc, flashloan_asset)
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
        and float(getattr(p, "tvl_usd", 0.0) or 0.0) >= float(os.getenv("DRY_RUN_LIVE_MIN_POOL_TVL_USD", "1000"))
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
    min_c2_profit = _env_float("DRY_RUN_LIVE_MIN_C2_PROFIT_USD", 2.0)
    min_spread_bps = _env_float("DRY_RUN_LIVE_MIN_SPREAD_BPS", 0.01)
    size_fractions = _env_size_fractions()

    for token in tokens:
        token_quotes = quotes.get(_addr_key(token["address"]), [])
        if len(token_quotes) < 2:
            continue
        for buy_pool, buy_price in sorted(token_quotes, key=lambda x: x[1]):
            for sell_pool, sell_price in sorted(token_quotes, key=lambda x: x[1], reverse=True):
                if getattr(buy_pool, "address", "") == getattr(sell_pool, "address", ""):
                    continue
                spread_bps = ((sell_price - buy_price) / buy_price) * 10_000.0
                if spread_bps <= min_spread_bps:
                    continue
                weakest_tvl = min(
                    float(getattr(buy_pool, "tvl_usd", 0.0) or 0.0),
                    float(getattr(sell_pool, "tvl_usd", 0.0) or 0.0),
                )
                if weakest_tvl <= 0.0:
                    continue
                max_flash_tvl_fraction = float(os.getenv("MAX_FLASH_TVL_FRACTION", "0.15"))
                max_flash_tvl_fraction = max(0.0, min(max_flash_tvl_fraction, 0.15))
                dynamic_flash_size_usd = weakest_tvl * max_flash_tvl_fraction
                best_size, size_curve = _evaluate_size_curve(
                    spread_bps=spread_bps,
                    weakest_tvl=weakest_tvl,
                    max_trade_size=dynamic_flash_size_usd,
                    gas_cost=gas_cost,
                    fractions=size_fractions,
                    provider_liquidity=provider_liquidity,
                    quote_out_usd=lambda amount, bp=buy_pool, sp=sell_pool, addr=token[
                        "address"
                    ]: _simulate_two_leg_v2_usd(monitor, bp, sp, addr, amount),
                )
                sized_trade = float(best_size["loan_size_usd"])
                if sized_trade <= 0.0:
                    continue
                c1_net = float(best_size["c1_net_usd"])
                c2_net = float(best_size["c2_evaluated_net_usd"])
                buy_pool_tvl = float(getattr(buy_pool, "tvl_usd", 0.0) or 0.0)
                sell_pool_tvl = float(getattr(sell_pool, "tvl_usd", 0.0) or 0.0)
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
                        "flashloan_asset_symbol": flashloan_asset["symbol"],
                        "flashloan_asset_address": flashloan_asset["address"],
                        "buy_price_usd": buy_price,
                        "sell_price_usd": sell_price,
                        "spread_bps": spread_bps,
                        "flash_size_usd": sized_trade,
                        "trade_size_usd": sized_trade,
                        "weakest_pool_tvl_usd": weakest_tvl,
                        "buy_pool_tvl_usd": buy_pool_tvl,
                        "sell_pool_tvl_usd": sell_pool_tvl,
                        "pool_usage_fraction": sized_trade / weakest_tvl,
                        "min_c1_profit_usd": min_c1_profit,
                        "min_c2_profit_usd": min_c2_profit,
                        "estimated_profit_usd": c1_net,
                        "post_c1_estimated_profit_usd": c2_net,
                        "size_optimizer": {
                            "fractions": size_fractions,
                            "selected": best_size,
                            "curve": size_curve,
                        },
                        "flashloan_liquidity": {
                            **provider_liquidity,
                            "selected_loan_size_usd": sized_trade,
                            "selected_size_available": (
                                bool(provider_liquidity.get("healthy", False))
                                and sized_trade <= float(provider_liquidity.get("max_available_usd", 0.0) or 0.0)
                            ),
                        },
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
