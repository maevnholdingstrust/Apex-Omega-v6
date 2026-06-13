from __future__ import annotations

import time
import os
from dataclasses import dataclass
from typing import Any, Mapping

from web3 import Web3

from .execution_compiler import ExecutionCompiler
from .polygon_market_registry import TOKENS, VENUES
from .route_graph import CycleRecord
from .route_step_encoder import validate_route_steps
from .swap_adapters import SwapRequest, UniversalSwapAdapter


_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_UNISWAP_V3_QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
_UNISWAP_V3_QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]
_CURVE_POOL_ABI = [
    {
        "name": "get_dy",
        "outputs": [{"type": "uint256", "name": ""}],
        "inputs": [
            {"type": "int128", "name": "i"},
            {"type": "int128", "name": "j"},
            {"type": "uint256", "name": "dx"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "get_dy_underlying",
        "outputs": [{"type": "uint256", "name": ""}],
        "inputs": [
            {"type": "int128", "name": "i"},
            {"type": "int128", "name": "j"},
            {"type": "uint256", "name": "dx"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass(frozen=True)
class ExpandedStrategyBuildResult:
    strikeable: bool
    reason: str
    strategy_output: dict[str, Any] | None
    compiled_payload_len: int = 0
    min_profit: int = 0
    diagnostics: dict[str, Any] | None = None


def _raw(amount: float, decimals: int) -> int:
    return max(1, int(amount * (10 ** decimals)))


def _min_out(amount: float, decimals: int, buffer_bps: float) -> int:
    safe = amount * (1.0 - buffer_bps / 10_000.0)
    return max(1, int(safe * (10 ** decimals)))


def _venue_and_fee(dex_label: str) -> tuple[str, int | None]:
    label = dex_label.lower()
    v2_aliases = {
        "qsv2": "quickswap_v2",
        "quickswap": "quickswap_v2",
        "quickswap_v2": "quickswap_v2",
        "sushi": "sushiswap_v2",
        "sushiswap": "sushiswap_v2",
        "sushiswap_v2": "sushiswap_v2",
        "apeswap": "apeswap_v2",
        "apeswap_v2": "apeswap_v2",
        "dfyn": "dfyn_v2",
        "dfyn_v2": "dfyn_v2",
        "jetswap": "jetswap_v2",
        "jetswap_v2": "jetswap_v2",
    }
    if label in v2_aliases:
        return v2_aliases[label], None
    if label.startswith("univ3_"):
        return "uniswap_v3", int(label.split("_", 1)[1])
    if label in {"curve", "curve_ss", "curve_stable"}:
        return "curve", None
    raise ValueError(f"expanded route leg {dex_label!r} is not executable by current adapters")


def _v2_live_amount_out(
    w3: Web3,
    router: str,
    amount_in: int,
    token_in: str,
    token_out: str,
) -> int:
    contract = w3.eth.contract(address=Web3.to_checksum_address(router), abi=_V2_ROUTER_ABI)
    amounts = contract.functions.getAmountsOut(
        int(amount_in),
        [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)],
    ).call()
    if len(amounts) < 2 or int(amounts[-1]) <= 0:
        raise ValueError("V2 router returned no output")
    return int(amounts[-1])


def _v3_live_amount_out(
    w3: Web3,
    amount_in: int,
    token_in: str,
    token_out: str,
    fee: int,
) -> int:
    quoter = w3.eth.contract(
        address=Web3.to_checksum_address(os.getenv("UNISWAP_V3_QUOTER_V2", _UNISWAP_V3_QUOTER_V2)),
        abi=_UNISWAP_V3_QUOTER_V2_ABI,
    )
    quoted = quoter.functions.quoteExactInputSingle(
        (
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(amount_in),
            int(fee),
            0,
        )
    ).call()
    amount_out = int(quoted[0])
    if amount_out <= 0:
        raise ValueError("Uniswap V3 quoter returned no output")
    return amount_out


def _curve_live_amount_out(
    w3: Web3,
    pool: str,
    i: int,
    j: int,
    amount_in: int,
) -> int:
    curve = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=_CURVE_POOL_ABI)
    errors: list[str] = []
    for fn_name in ("get_dy", "get_dy_underlying"):
        try:
            out = int(getattr(curve.functions, fn_name)(int(i), int(j), int(amount_in)).call())
            if out > 0:
                return out
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn_name}: {exc}")
    raise ValueError("Curve live quote failed: " + " | ".join(errors))


def build_expanded_strategy_output_from_cycle(
    cycle: CycleRecord,
    token_prices_usd: Mapping[str, float],
    executor_address: str,
    min_net_profit_usd: float = 2.0,
    minout_buffer_bps: float = 25.0,
    rpc_url: str | None = None,
    live_quote_buffer_bps: float = 50.0,
) -> ExpandedStrategyBuildResult:
    if not cycle.profitable or cycle.net_profit_usd < min_net_profit_usd:
        return ExpandedStrategyBuildResult(False, "expanded route below owner-net profit threshold", None)
    if len(cycle.tokens) != cycle.hop_count + 1:
        return ExpandedStrategyBuildResult(False, "expanded route token path length mismatch", None)
    if not (
        len(cycle.dexes)
        == len(cycle.pools)
        == len(cycle.leg_amounts_in)
        == len(cycle.leg_amounts_out)
        == cycle.hop_count
    ):
        return ExpandedStrategyBuildResult(False, "expanded route missing per-hop executable trace", None)

    start_symbol = cycle.tokens[0]
    if start_symbol not in TOKENS:
        return ExpandedStrategyBuildResult(False, f"unknown start token {start_symbol}", None)
    start_token = TOKENS[start_symbol]
    start_price = float(token_prices_usd.get(start_symbol, 0.0) or 0.0)
    if start_price <= 0.0:
        return ExpandedStrategyBuildResult(False, "missing start-token USD price for min-profit conversion", None)
    flash_amount_raw = _raw(cycle.amount_in, start_token.decimals)

    receiver = Web3.to_checksum_address(executor_address)
    deadline = int(time.time()) + 90
    adapter = UniversalSwapAdapter()
    steps: list[dict[str, Any]] = []
    quote_w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10})) if rpc_url else None
    live_quote_diagnostics: list[dict[str, Any]] = []
    previous_guaranteed_out: int | None = None

    for idx in range(cycle.hop_count):
        token_in_symbol = cycle.tokens[idx]
        token_out_symbol = cycle.tokens[idx + 1]
        if token_in_symbol not in TOKENS or token_out_symbol not in TOKENS:
            return ExpandedStrategyBuildResult(False, "expanded route contains token outside execution registry", None)
        token_in = TOKENS[token_in_symbol]
        token_out = TOKENS[token_out_symbol]
        try:
            venue, fee = _venue_and_fee(cycle.dexes[idx])
            discovered_amount_in_raw = _raw(cycle.leg_amounts_in[idx], token_in.decimals)
            amount_in_raw = discovered_amount_in_raw
            input_scale = 1.0
            if previous_guaranteed_out is not None and discovered_amount_in_raw > previous_guaranteed_out:
                amount_in_raw = previous_guaranteed_out
                input_scale = amount_in_raw / discovered_amount_in_raw
            discovered_min_out = _min_out(cycle.leg_amounts_out[idx] * input_scale, token_out.decimals, minout_buffer_bps)
            min_amount_out = discovered_min_out
            venue_spec = VENUES[venue]
            if quote_w3 is not None and venue_spec.kind == "v2":
                router = venue_spec.router
                if not router:
                    return ExpandedStrategyBuildResult(False, "V2 venue missing router for live quote", None, diagnostics={"leg": idx, "venue": venue})
                live_out = _v2_live_amount_out(quote_w3, router, amount_in_raw, token_in.address, token_out.address)
                live_min_out = max(1, int(live_out * (1.0 - live_quote_buffer_bps / 10_000.0)))
                if live_min_out <= 0:
                    return ExpandedStrategyBuildResult(False, "V2 live quote produced zero minOut", None, diagnostics={"leg": idx, "venue": venue})
                min_amount_out = min(discovered_min_out, live_min_out)
                live_quote_diagnostics.append(
                    {
                        "leg": idx,
                        "venue": venue,
                        "token_in": token_in_symbol,
                        "token_out": token_out_symbol,
                        "amount_in": amount_in_raw,
                        "discovered_amount_in": discovered_amount_in_raw,
                        "input_scale": input_scale,
                        "discovered_min_out": discovered_min_out,
                        "live_router_out": live_out,
                        "live_min_out": live_min_out,
                        "compiled_min_out": min_amount_out,
                    }
                )
            elif quote_w3 is not None and venue_spec.kind == "v3":
                if fee is None:
                    return ExpandedStrategyBuildResult(False, "Uniswap V3 leg missing fee tier for live quote", None, diagnostics={"leg": idx, "venue": venue})
                live_out = _v3_live_amount_out(quote_w3, amount_in_raw, token_in.address, token_out.address, int(fee))
                live_min_out = max(1, int(live_out * (1.0 - live_quote_buffer_bps / 10_000.0)))
                min_amount_out = min(discovered_min_out, live_min_out)
                live_quote_diagnostics.append(
                    {
                        "leg": idx,
                        "venue": venue,
                        "token_in": token_in_symbol,
                        "token_out": token_out_symbol,
                        "fee": int(fee),
                        "amount_in": amount_in_raw,
                        "discovered_amount_in": discovered_amount_in_raw,
                        "input_scale": input_scale,
                        "discovered_min_out": discovered_min_out,
                        "live_router_out": live_out,
                        "live_min_out": live_min_out,
                        "compiled_min_out": min_amount_out,
                    }
                )
            elif venue_spec.kind == "curve":
                if idx >= len(cycle.swap_0_to_1):
                    return ExpandedStrategyBuildResult(
                        False,
                        "Curve route missing coin direction metadata",
                        None,
                        diagnostics={"leg": idx, "venue": venue, "pool": cycle.pools[idx]},
                    )
                coin_i, coin_j = (0, 1) if cycle.swap_0_to_1[idx] else (1, 0)
                if quote_w3 is not None:
                    live_out = _curve_live_amount_out(quote_w3, cycle.pools[idx], coin_i, coin_j, amount_in_raw)
                    live_min_out = max(1, int(live_out * (1.0 - live_quote_buffer_bps / 10_000.0)))
                    min_amount_out = min(discovered_min_out, live_min_out)
                    live_quote_diagnostics.append(
                        {
                            "leg": idx,
                            "venue": venue,
                            "token_in": token_in_symbol,
                            "token_out": token_out_symbol,
                            "pool": cycle.pools[idx],
                            "i": coin_i,
                            "j": coin_j,
                            "amount_in": amount_in_raw,
                            "discovered_amount_in": discovered_amount_in_raw,
                            "input_scale": input_scale,
                            "discovered_min_out": discovered_min_out,
                            "live_router_out": live_out,
                            "live_min_out": live_min_out,
                            "compiled_min_out": min_amount_out,
                        }
                    )
            steps.append(
                adapter.build_step(
                    SwapRequest(
                        venue,
                        token_in.address,
                        token_out.address,
                        amount_in_raw,
                        min_amount_out,
                        receiver,
                        deadline,
                        fee=fee,
                        pool=cycle.pools[idx] if venue_spec.kind == "curve" else None,
                        extra={"i": coin_i, "j": coin_j} if venue_spec.kind == "curve" else None,
                    )
                )
            )
            previous_guaranteed_out = min_amount_out
        except Exception as exc:  # noqa: BLE001
            return ExpandedStrategyBuildResult(False, "expanded route leg is not payload-buildable", None, diagnostics={"error": str(exc), "leg": idx})

    validate_route_steps(steps)
    min_profit_token = max(cycle.net_profit_usd / start_price, 0.000001)
    if quote_w3 is not None and previous_guaranteed_out is not None:
        flash_fee_token = max(float(cycle.flash_fee_usd) / start_price, 0.0)
        gas_cost_token = max(float(cycle.gas_cost_usd) / start_price, 0.0)
        min_owner_profit_token = max(float(min_net_profit_usd) / start_price, 0.0)
        flash_fee_raw = _raw(flash_fee_token, start_token.decimals) if flash_fee_token > 0 else 0
        gas_cost_raw = _raw(gas_cost_token, start_token.decimals) if gas_cost_token > 0 else 0
        min_owner_profit_raw = _raw(min_owner_profit_token, start_token.decimals)
        required_final_raw = flash_amount_raw + flash_fee_raw + gas_cost_raw + min_owner_profit_raw
        if previous_guaranteed_out < required_final_raw:
            return ExpandedStrategyBuildResult(
                False,
                "expanded route live quotes below repayment plus owner-profit threshold",
                None,
                diagnostics={
                    "asset": start_symbol,
                    "flash_amount": flash_amount_raw,
                    "flash_fee": flash_fee_raw,
                    "gas_cost": gas_cost_raw,
                    "min_owner_profit": min_owner_profit_raw,
                    "required_final": required_final_raw,
                    "quoted_final": previous_guaranteed_out,
                    "live_quotes": live_quote_diagnostics,
                },
            )
        live_surplus_raw = previous_guaranteed_out - flash_amount_raw - flash_fee_raw - gas_cost_raw
        min_profit_token = live_surplus_raw / float(10 ** start_token.decimals)
    weakest_pool_tvl_usd = max(float(cycle.trade_size_usd) / 0.15, float(cycle.trade_size_usd))
    strategy_output = {
        "asset": start_token.address,
        "executor_address": receiver,
        "flash_loan_receiver": receiver,
        "flash_loan_amount": flash_amount_raw,
        "min_profit": _raw(min_profit_token, start_token.decimals),
        "gas_reserve_asset": 0,
        "dex_fee_reserve_asset": 0,
        "steps": steps,
        "opportunity": {
            "net_profit_usd": float(cycle.net_profit_usd),
            "gross_profit_usd": float(cycle.gross_profit_usd),
            "flashloan_fee_usd": float(cycle.flash_fee_usd),
            "gas_cost_usd": float(cycle.gas_cost_usd),
            "loan_amount": float(cycle.trade_size_usd),
            "flash_loan_amount_usd": float(cycle.trade_size_usd),
            "weakest_pool_tvl_usd": weakest_pool_tvl_usd,
            "pool_tvl_usd": weakest_pool_tvl_usd,
            "final_output_usd": float(cycle.trade_size_usd + cycle.net_profit_usd),
            "minimum_final_output": float(cycle.trade_size_usd + cycle.flash_fee_usd + min_net_profit_usd),
            "hop_count": int(cycle.hop_count),
            "route_tokens": "->".join(cycle.tokens),
            "route_pools": "->".join(cycle.pools),
            "route_dexes": "->".join(cycle.dexes),
        },
    }
    compiled = ExecutionCompiler().compile_for_institutional(strategy_output)
    return ExpandedStrategyBuildResult(
        True,
        "expanded route strategy wired",
        strategy_output,
        compiled_payload_len=len(compiled.encoded_payload),
        min_profit=compiled.min_profit,
        diagnostics={
            "steps": len(steps),
            "net_profit_usd": float(cycle.net_profit_usd),
            "trade_size_usd": float(cycle.trade_size_usd),
            "asset": start_symbol,
            "live_quotes": live_quote_diagnostics,
        },
    )
