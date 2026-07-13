"""Live quote query layer for V2 and V3 execution legs.

This module performs token-native quote calls only.  It never fabricates prices,
never defaults a quote amount, and never converts dashboard mid-prices into an
execution quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from eth_abi import decode, encode
from web3 import Web3

from .dex_execution_adapters import DexFamily, LiveLegQuote


V2_GET_AMOUNTS_OUT_SIG = "getAmountsOut(uint256,address[])"
V3_QUOTER_V1_SIG = "quoteExactInputSingle(address,address,uint24,uint256,uint160)"
V3_QUOTER_V2_SIG = "quoteExactInputSingle((address,address,uint256,uint24,uint160))"


@dataclass(frozen=True)
class V2QuoteRequest:
    router: str
    token_in: str
    token_out: str
    amount_in: int
    receiver: str
    deadline: int
    slippage_bps: int


@dataclass(frozen=True)
class V3QuoteRequest:
    quoter: str
    router: str
    token_in: str
    token_out: str
    fee: int
    amount_in: int
    receiver: str
    deadline: int
    slippage_bps: int
    sqrt_price_limit_x96: int = 0
    quoter_version: int = 1


class LiveDexQuoteClient:
    def __init__(self, rpc_url: str | None = None, web3: Web3 | None = None) -> None:
        self.w3 = web3 or Web3(Web3.HTTPProvider(rpc_url or "https://polygon-rpc.com/"))

    def quote_v2_exact_in(self, req: V2QuoteRequest) -> LiveLegQuote:
        _validate_bps(req.slippage_bps)
        _validate_amount(req.amount_in, "amount_in")
        router = _addr(req.router)
        token_in = _addr(req.token_in)
        token_out = _addr(req.token_out)
        receiver = _addr(req.receiver)
        payload = _selector(V2_GET_AMOUNTS_OUT_SIG) + encode(
            ["uint256", "address[]"],
            [int(req.amount_in), [token_in, token_out]],
        )
        raw = self._call(router, payload)
        amounts = decode(["uint256[]"], raw)[0]
        if len(amounts) != 2:
            raise ValueError("V2 router returned unexpected amounts length")
        amount_out = int(amounts[-1])
        if amount_out <= 0:
            raise ValueError("V2 quote returned non-positive output")
        return LiveLegQuote(
            family=DexFamily.V2,
            venue=router,
            token_in=token_in,
            token_out=token_out,
            amount_in=int(req.amount_in),
            min_amount_out=_apply_slippage_floor(amount_out, req.slippage_bps),
            receiver=receiver,
            deadline=int(req.deadline),
            quote_amount_out=amount_out,
        )

    def quote_v3_exact_in(self, req: V3QuoteRequest) -> LiveLegQuote:
        _validate_bps(req.slippage_bps)
        _validate_amount(req.amount_in, "amount_in")
        _validate_amount(req.fee, "fee")
        quoter = _addr(req.quoter)
        router = _addr(req.router)
        token_in = _addr(req.token_in)
        token_out = _addr(req.token_out)
        receiver = _addr(req.receiver)
        sqrt_limit = int(req.sqrt_price_limit_x96)
        if sqrt_limit < 0:
            raise ValueError("sqrt_price_limit_x96 cannot be negative")

        if int(req.quoter_version) == 2:
            payload = _selector(V3_QUOTER_V2_SIG) + encode(
                ["(address,address,uint256,uint24,uint160)"],
                [(token_in, token_out, int(req.amount_in), int(req.fee), sqrt_limit)],
            )
            raw = self._call(quoter, payload)
            amount_out = int(decode(["uint256", "uint160", "uint32", "uint256"], raw)[0])
        else:
            payload = _selector(V3_QUOTER_V1_SIG) + encode(
                ["address", "address", "uint24", "uint256", "uint160"],
                [token_in, token_out, int(req.fee), int(req.amount_in), sqrt_limit],
            )
            raw = self._call(quoter, payload)
            amount_out = int(decode(["uint256"], raw)[0])

        if amount_out <= 0:
            raise ValueError("V3 quote returned non-positive output")
        return LiveLegQuote(
            family=DexFamily.V3,
            venue=router,
            token_in=token_in,
            token_out=token_out,
            amount_in=int(req.amount_in),
            min_amount_out=_apply_slippage_floor(amount_out, req.slippage_bps),
            receiver=receiver,
            deadline=int(req.deadline),
            fee=int(req.fee),
            sqrt_price_limit_x96=sqrt_limit,
            quote_amount_out=amount_out,
        )

    def quote_many(self, requests: Iterable[V2QuoteRequest | V3QuoteRequest]) -> list[LiveLegQuote]:
        out: list[LiveLegQuote] = []
        for req in requests:
            if isinstance(req, V2QuoteRequest):
                out.append(self.quote_v2_exact_in(req))
            elif isinstance(req, V3QuoteRequest):
                out.append(self.quote_v3_exact_in(req))
            else:
                raise TypeError(f"unsupported quote request: {type(req)!r}")
        return out

    def _call(self, to: str, payload: bytes) -> bytes:
        try:
            raw = self.w3.eth.call({"to": to, "data": Web3.to_hex(payload)})
        except Exception as exc:
            raise RuntimeError(f"quote call failed for {to}: {exc}") from exc
        if raw in (b"", "0x", None):
            raise RuntimeError(f"quote call returned empty data for {to}")
        return raw


def quote_v2_v3_route(client: LiveDexQuoteClient, requests: Sequence[V2QuoteRequest | V3QuoteRequest]) -> list[LiveLegQuote]:
    quotes = client.quote_many(requests)
    if len(quotes) < 2:
        raise ValueError("route quote requires at least two legs")
    for left, right in zip(quotes, quotes[1:]):
        if left.token_out.lower() != right.token_in.lower():
            raise ValueError(f"broken quoted route chain: {left.token_out} != {right.token_in}")
    return quotes


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _addr(value: str) -> str:
    if not isinstance(value, str) or not Web3.is_address(value):
        raise ValueError(f"invalid EVM address: {value!r}")
    return Web3.to_checksum_address(value)


def _validate_amount(value: int, label: str) -> None:
    if int(value) <= 0:
        raise ValueError(f"{label} must be positive")


def _validate_bps(value: int) -> None:
    bps = int(value)
    if bps < 0 or bps >= 10_000:
        raise ValueError("slippage_bps must be in [0, 10000)")


def _apply_slippage_floor(amount_out: int, slippage_bps: int) -> int:
    floor = int(amount_out) * (10_000 - int(slippage_bps)) // 10_000
    if floor <= 0:
        raise ValueError("slippage floor is non-positive")
    return floor
