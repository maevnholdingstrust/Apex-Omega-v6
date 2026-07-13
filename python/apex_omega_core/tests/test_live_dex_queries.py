from eth_abi import encode
from web3 import Web3

from apex_omega_core.core.live_dex_queries import (
    V2QuoteRequest,
    V3QuoteRequest,
    LiveDexQuoteClient,
    quote_v2_v3_route,
)

A = "0x0000000000000000000000000000000000000001"
B = "0x0000000000000000000000000000000000000002"
C = "0x0000000000000000000000000000000000000003"
ROUTER = "0x0000000000000000000000000000000000000004"
QUOTER = "0x0000000000000000000000000000000000000005"
RECEIVER = "0x0000000000000000000000000000000000000006"


class _Eth:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def call(self, tx):
        self.calls.append(tx)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class _W3:
    def __init__(self, outputs):
        self.eth = _Eth(outputs)


def test_v2_quote_returns_live_leg_quote():
    raw = encode(["uint256[]"], [[1000, 950]])
    client = LiveDexQuoteClient(web3=_W3([raw]))
    quote = client.quote_v2_exact_in(
        V2QuoteRequest(router=ROUTER, token_in=A, token_out=B, amount_in=1000, receiver=RECEIVER, deadline=999, slippage_bps=100)
    )
    assert quote.amount_in == 1000
    assert quote.quote_amount_out == 950
    assert quote.min_amount_out == 940
    assert quote.token_in == Web3.to_checksum_address(A)


def test_v3_v1_quote_returns_live_leg_quote():
    raw = encode(["uint256"], [1010])
    client = LiveDexQuoteClient(web3=_W3([raw]))
    quote = client.quote_v3_exact_in(
        V3QuoteRequest(quoter=QUOTER, router=ROUTER, token_in=B, token_out=A, fee=500, amount_in=900, receiver=RECEIVER, deadline=999, slippage_bps=50)
    )
    assert quote.fee == 500
    assert quote.quote_amount_out == 1010
    assert quote.min_amount_out == 1004


def test_v3_v2_quote_returns_live_leg_quote():
    raw = encode(["uint256", "uint160", "uint32", "uint256"], [1010, 0, 0, 0])
    client = LiveDexQuoteClient(web3=_W3([raw]))
    quote = client.quote_v3_exact_in(
        V3QuoteRequest(quoter=QUOTER, router=ROUTER, token_in=B, token_out=A, fee=500, amount_in=900, receiver=RECEIVER, deadline=999, slippage_bps=50, quoter_version=2)
    )
    assert quote.quote_amount_out == 1010
    assert quote.min_amount_out == 1004


def test_quote_v2_v3_route_validates_chain():
    client = LiveDexQuoteClient(web3=_W3([encode(["uint256[]"], [[1000, 950]]), encode(["uint256"], [1010])]))
    quotes = quote_v2_v3_route(
        client,
        [
            V2QuoteRequest(router=ROUTER, token_in=A, token_out=B, amount_in=1000, receiver=RECEIVER, deadline=999, slippage_bps=100),
            V3QuoteRequest(quoter=QUOTER, router=ROUTER, token_in=B, token_out=A, fee=500, amount_in=900, receiver=RECEIVER, deadline=999, slippage_bps=50),
        ],
    )
    assert len(quotes) == 2
    assert quotes[0].token_out.lower() == quotes[1].token_in.lower()
