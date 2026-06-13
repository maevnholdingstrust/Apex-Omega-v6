from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace

import pytest

from apex_omega_core.core import multi_market_scanner as mms
from apex_omega_core.core.multi_market_scanner import MarketQuote
from apex_omega_core.core.polygon_market_registry import TokenSpec


def test_quotes_for_pair_logs_and_continues_on_venue_fetch_exception(monkeypatch, caplog):
    venues = OrderedDict(
        {
            "broken_v2": SimpleNamespace(kind="v2", supported=True),
            "ok_v3": SimpleNamespace(kind="v3", supported=True),
        }
    )
    monkeypatch.setattr(mms, "SUPPORTED_EXECUTION_VENUES", venues)

    def broken_fetch(*_args, **_kwargs):
        raise RuntimeError("rpc timeout")

    def ok_fetch(*_args, **_kwargs):
        return MarketQuote(
            venue="ok_v3",
            pool="0xPool",
            kind="v3",
            fee_bps=5.0,
            base_symbol="WMATIC",
            quote_symbol="USDC",
            price_quote_per_base=1.0,
            liquidity_hint=1_000_000.0,
        )

    monkeypatch.setattr(mms, "_fetch_v2_quote", broken_fetch)
    monkeypatch.setattr(mms, "_fetch_v3_quote", ok_fetch)

    with caplog.at_level("WARNING"):
        quotes = mms.quotes_for_pair("WMATIC", "USDC")

    assert len(quotes) == 1
    assert quotes[0].venue == "ok_v3"
    assert "quote_fetch_failed venue=broken_v2 pair=WMATIC/USDC" in caplog.text


def test_scan_multi_market_derives_execution_supported_from_venues(monkeypatch):
    monkeypatch.setattr(
        mms,
        "TOKENS",
        OrderedDict(
            {
                "AAA": TokenSpec("AAA", "0x0000000000000000000000000000000000000001", 18),
                "BBB": TokenSpec("BBB", "0x0000000000000000000000000000000000000002", 18),
            }
        ),
    )

    monkeypatch.setattr(
        mms,
        "SUPPORTED_EXECUTION_VENUES",
        {
            "buy_venue": SimpleNamespace(kind="v2", supported=True),
            "sell_venue": SimpleNamespace(kind="v2", supported=False),
        },
    )

    monkeypatch.setattr(
        mms,
        "quotes_for_pair",
        lambda *_args, **_kwargs: [
            MarketQuote("buy_venue", "0xBuy", "v2", 30.0, "AAA", "BBB", 100.0, 1_000_000.0),
            MarketQuote("sell_venue", "0xSell", "v2", 30.0, "AAA", "BBB", 101.0, 1_000_000.0),
        ],
    )

    opportunities = mms.scan_multi_market(max_pairs=1)

    assert len(opportunities) == 1
    assert opportunities[0].execution_supported is False


def test_scan_usdc_value_routes_uses_post_gas_threshold(monkeypatch):
    monkeypatch.setattr(
        mms,
        "TOKENS",
        OrderedDict(
            {
                "USDC": TokenSpec("USDC", "0x0000000000000000000000000000000000000010", 6),
                "MID": TokenSpec("MID", "0x0000000000000000000000000000000000000011", 18),
            }
        ),
    )

    monkeypatch.setattr(
        mms,
        "quotes_for_pair",
        lambda *_args, **_kwargs: [
            MarketQuote("buy_venue", "0xBuy", "v2", 30.0, "MID", "USDC", 1.0, 1_000_000.0),
            MarketQuote("sell_venue", "0xSell", "v2", 30.0, "MID", "USDC", 1.01, 1_000_000.0),
        ],
    )

    routes = mms.scan_usdc_value_routes(
        start_amount_usdc=100.0,
        min_net_profit_usdc=0.2,
        max_mid_tokens=1,
        gas_cost_usdc=0.55,
        flash_fee_bps=9.0,
        risk_buffer_usdc=0.0,
        mempool_degradation_bps=25.0,
    )

    assert len(routes) == 1
    assert routes[0].gross_profit_usdc > 0.0
    assert routes[0].net_profit_usdc > 0.2
    assert (routes[0].net_profit_usdc - 0.55) < 0.2
    assert routes[0].decision == "IDLE_COSTS_EXCEED_EDGE"
