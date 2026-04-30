"""Tests for token_universe.py."""

from __future__ import annotations

import pytest

from apex_omega_core.core.token_universe import (
    POLYGON_CHAIN_ID,
    POLYGON_CORE_TOKENS,
    TokenUniverse,
    _checksum,
)
from apex_omega_core.core.types import TokenMeta


# ── _checksum helper ──────────────────────────────────────────────────────────


class TestChecksum:
    _WMATIC = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"

    def test_round_trips_checksummed_address(self) -> None:
        result = _checksum(self._WMATIC)
        assert result == self._WMATIC

    def test_normalises_lowercase_input(self) -> None:
        result = _checksum(self._WMATIC.lower())
        # Should be same length and start with 0x
        assert result.startswith("0x")
        assert len(result) == 42

    def test_normalises_uppercase_input(self) -> None:
        result = _checksum(self._WMATIC.upper().replace("0X", "0x"))
        assert result.startswith("0x")
        assert len(result) == 42

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError):
            _checksum("0x1234")

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError):
            _checksum("0x" + "Z" * 40)


# ── POLYGON_CORE_TOKENS catalogue ─────────────────────────────────────────────


class TestPolygonCoreTokens:
    def test_non_empty(self) -> None:
        assert len(POLYGON_CORE_TOKENS) > 0

    def test_all_have_symbol(self) -> None:
        for token in POLYGON_CORE_TOKENS:
            assert token.symbol, f"Missing symbol for {token.address}"

    def test_all_have_polygon_chain_id(self) -> None:
        for token in POLYGON_CORE_TOKENS:
            assert token.chain_id == POLYGON_CHAIN_ID

    def test_all_addresses_are_valid_length(self) -> None:
        for token in POLYGON_CORE_TOKENS:
            assert token.address.startswith("0x"), token.address
            assert len(token.address) == 42, token.address

    def test_wmatic_present(self) -> None:
        symbols = {t.symbol for t in POLYGON_CORE_TOKENS}
        assert "WMATIC" in symbols

    def test_usdc_present(self) -> None:
        symbols = {t.symbol for t in POLYGON_CORE_TOKENS}
        assert "USDC" in symbols

    def test_no_duplicate_addresses(self) -> None:
        addresses = [t.address.lower() for t in POLYGON_CORE_TOKENS]
        assert len(addresses) == len(set(addresses))


# ── TokenUniverse construction ────────────────────────────────────────────────


class TestTokenUniverseConstruction:
    def test_default_seeded_with_core_tokens(self) -> None:
        universe = TokenUniverse()
        assert universe.size() == len(POLYGON_CORE_TOKENS)

    def test_empty_universe_from_empty_list(self) -> None:
        universe = TokenUniverse(tokens=[])
        assert universe.size() == 0

    def test_custom_seed_list(self) -> None:
        tokens = [
            TokenMeta(address="0x" + "a" * 40, decimals=18, symbol="AAA", chain_id=137),
        ]
        universe = TokenUniverse(tokens=tokens)
        assert universe.size() == 1

    def test_seed_list_not_mutated(self) -> None:
        original = list(POLYGON_CORE_TOKENS)
        universe = TokenUniverse()
        # Adding to the universe must not affect the original list.
        universe.add(TokenMeta(address="0x" + "b" * 40, decimals=18, symbol="BBB", chain_id=137))
        assert POLYGON_CORE_TOKENS == original

    def test_duplicate_in_seed_deduped(self) -> None:
        wmatic = POLYGON_CORE_TOKENS[0]
        universe = TokenUniverse(tokens=[wmatic, wmatic])
        assert universe.size() == 1


# ── add / remove / get ────────────────────────────────────────────────────────

_FAKE_ADDR = "0x" + "d" * 40


class TestTokenUniverseCRUD:
    def test_add_new_token(self) -> None:
        universe = TokenUniverse(tokens=[])
        token = TokenMeta(address=_FAKE_ADDR, decimals=18, symbol="FAKE", chain_id=137)
        universe.add(token)
        assert universe.size() == 1

    def test_add_overwrites_existing(self) -> None:
        token_v1 = TokenMeta(address=_FAKE_ADDR, decimals=18, symbol="FAKE", chain_id=137)
        token_v2 = TokenMeta(address=_FAKE_ADDR, decimals=6, symbol="FAKE2", chain_id=137)
        universe = TokenUniverse(tokens=[token_v1])
        universe.add(token_v2)
        assert universe.size() == 1
        result = universe.get(_FAKE_ADDR)
        assert result is not None
        assert result.symbol == "FAKE2"

    def test_remove_existing_token(self) -> None:
        token = TokenMeta(address=_FAKE_ADDR, decimals=18, symbol="FAKE", chain_id=137)
        universe = TokenUniverse(tokens=[token])
        removed = universe.remove(_FAKE_ADDR)
        assert removed is True
        assert universe.size() == 0

    def test_remove_non_existent_returns_false(self) -> None:
        universe = TokenUniverse(tokens=[])
        assert universe.remove(_FAKE_ADDR) is False

    def test_get_existing_token(self) -> None:
        wmatic = POLYGON_CORE_TOKENS[0]
        universe = TokenUniverse()
        result = universe.get(wmatic.address)
        assert result is not None
        assert result.symbol == wmatic.symbol

    def test_get_missing_token_returns_none(self) -> None:
        universe = TokenUniverse(tokens=[])
        assert universe.get(_FAKE_ADDR) is None

    def test_contains_true(self) -> None:
        universe = TokenUniverse()
        assert universe.contains(POLYGON_CORE_TOKENS[0].address) is True

    def test_contains_false(self) -> None:
        universe = TokenUniverse(tokens=[])
        assert universe.contains(_FAKE_ADDR) is False

    def test_case_insensitive_get(self) -> None:
        universe = TokenUniverse()
        addr = POLYGON_CORE_TOKENS[0].address
        assert universe.get(addr.lower()) is not None
        assert universe.get(addr.upper()) is not None


# ── all() / addresses() ───────────────────────────────────────────────────────


class TestTokenUniverseQueries:
    def test_all_returns_all_tokens(self) -> None:
        universe = TokenUniverse()
        result = universe.all()
        assert len(result) == len(POLYGON_CORE_TOKENS)

    def test_all_returns_sorted_by_address(self) -> None:
        universe = TokenUniverse()
        addresses = [t.address for t in universe.all()]
        assert addresses == sorted(addresses)

    def test_addresses_returns_sorted_list(self) -> None:
        universe = TokenUniverse()
        addresses = universe.addresses()
        assert addresses == sorted(addresses)

    def test_filter_by_chain_returns_correct_subset(self) -> None:
        extra = TokenMeta(address="0x" + "e" * 40, decimals=18, symbol="ETH_TOK", chain_id=1)
        universe = TokenUniverse()
        universe.add(extra)
        polygon_tokens = universe.filter_by_chain(POLYGON_CHAIN_ID)
        assert all(t.chain_id == POLYGON_CHAIN_ID for t in polygon_tokens)
        eth_tokens = universe.filter_by_chain(1)
        assert len(eth_tokens) == 1

    def test_size_reflects_adds_and_removes(self) -> None:
        universe = TokenUniverse(tokens=[])
        assert universe.size() == 0
        token = TokenMeta(address=_FAKE_ADDR, decimals=18, symbol="X", chain_id=137)
        universe.add(token)
        assert universe.size() == 1
        universe.remove(_FAKE_ADDR)
        assert universe.size() == 0


# ── merge ─────────────────────────────────────────────────────────────────────


class TestTokenUniverseMerge:
    def test_merge_adds_new_tokens(self) -> None:
        a = TokenUniverse(tokens=[])
        b = TokenUniverse()
        a.merge(b)
        assert a.size() == b.size()

    def test_merge_does_not_overwrite_existing(self) -> None:
        addr = POLYGON_CORE_TOKENS[0].address
        token_v1 = TokenMeta(address=addr, decimals=18, symbol="ORIGINAL", chain_id=137)
        token_v2 = TokenMeta(address=addr, decimals=6, symbol="OVERWRITE", chain_id=137)
        a = TokenUniverse(tokens=[token_v1])
        b = TokenUniverse(tokens=[token_v2])
        a.merge(b)
        assert a.get(addr) is not None
        assert a.get(addr).symbol == "ORIGINAL"

    def test_merge_with_empty_is_no_op(self) -> None:
        universe = TokenUniverse()
        original_size = universe.size()
        universe.merge(TokenUniverse(tokens=[]))
        assert universe.size() == original_size


# ── Iteration protocol ────────────────────────────────────────────────────────


class TestTokenUniverseProtocol:
    def test_iter_yields_all_tokens(self) -> None:
        universe = TokenUniverse()
        result = list(universe)
        assert len(result) == universe.size()

    def test_len_returns_size(self) -> None:
        universe = TokenUniverse()
        assert len(universe) == universe.size()

    def test_contains_operator(self) -> None:
        universe = TokenUniverse()
        assert POLYGON_CORE_TOKENS[0].address in universe
        assert _FAKE_ADDR not in universe

    def test_contains_operator_non_string(self) -> None:
        universe = TokenUniverse()
        assert 42 not in universe  # type: ignore[operator]

    def test_contains_operator_invalid_address_string(self) -> None:
        """An invalid-format string (triggers ValueError in normalise) returns False."""
        universe = TokenUniverse()
        assert "not-an-address" not in universe  # type: ignore[operator]
