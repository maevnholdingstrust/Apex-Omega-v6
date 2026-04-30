"""Token Universe — curated registry of tokens the scanner watches.

Provides:

* ``POLYGON_CORE_TOKENS`` — hard-coded list of well-known Polygon-PoS tokens
  (WMATIC/POL, USDC, USDT, WETH, WBTC, DAI, LINK, AAVE, …).
* :class:`TokenUniverse` — dict-backed registry of :class:`~.types.TokenMeta`
  entries, keyed by EIP-55 checksummed address.

The registry is intentionally pure-Python with no I/O.  Callers that need to
persist or refresh the universe should handle that externally (e.g. in the
``PolygonDEXMonitor`` refresh cycle) and call :meth:`TokenUniverse.add` /
:meth:`TokenUniverse.merge` to update in place.
"""

from __future__ import annotations

from typing import Dict, Iterable, Iterator, List, Optional

from .types import TokenMeta

# ---------------------------------------------------------------------------
# Chain constant
# ---------------------------------------------------------------------------

POLYGON_CHAIN_ID: int = 137

# ---------------------------------------------------------------------------
# Well-known Polygon token catalogue
# ---------------------------------------------------------------------------

#: Curated seed list of high-liquidity Polygon-PoS tokens.
#: Addresses are EIP-55 checksummed and verified against the Polygon network.
POLYGON_CORE_TOKENS: List[TokenMeta] = [
    TokenMeta(
        address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        decimals=18,
        symbol="WMATIC",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        decimals=6,
        symbol="USDC",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        decimals=6,
        symbol="USDT",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        decimals=18,
        symbol="WETH",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
        decimals=8,
        symbol="WBTC",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
        decimals=18,
        symbol="DAI",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",
        decimals=18,
        symbol="LINK",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
        decimals=18,
        symbol="AAVE",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x3A58a54C066FdC0f2D55FC9C89F0415C92eBf3C4",
        decimals=18,
        symbol="stMATIC",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
        decimals=6,
        symbol="FRAX",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0xb33EaAd8d922B1083446DC23f610c2567fB5180f",
        decimals=18,
        symbol="UNI",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x172370d5Cd63279eFa6d502DAB29171933a610AF",
        decimals=18,
        symbol="CRV",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0x831753DD7087CaC61aB5644b308642cc1c33Dc13",
        decimals=18,
        symbol="QUICK",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0xdAb529f40E671A1D4bF91361c21bf9f0C9712ab7",
        decimals=18,
        symbol="BUSD",
        chain_id=POLYGON_CHAIN_ID,
    ),
    TokenMeta(
        address="0xE0339c80fFDE91F3e20494Df88d4206D86024cdF",
        decimals=18,
        symbol="EURS",
        chain_id=POLYGON_CHAIN_ID,
    ),
]

# ---------------------------------------------------------------------------
# Address normalisation helper
# ---------------------------------------------------------------------------


def _checksum(address: str) -> str:
    """Return the EIP-55 checksummed form of ``address``.

    Uses a lightweight hex-based algorithm so there is no dependency on web3
    at import time.  Raises ``ValueError`` for non-hex or wrong-length inputs.
    """
    address = address.strip()
    if address.startswith(("0x", "0X")):
        hex_part = address[2:]
    else:
        hex_part = address

    if len(hex_part) != 40:
        raise ValueError(f"Invalid EVM address length: {address!r}")

    try:
        int(hex_part, 16)
    except ValueError:
        raise ValueError(f"Non-hex characters in EVM address: {address!r}")

    hex_lower = hex_part.lower()

    # Keccak-256 of the lower-case hex string.
    try:
        from eth_hash.auto import keccak  # type: ignore[import]

        digest = keccak(hex_lower.encode())
        hash_hex = digest.hex()
    except Exception:
        # Fallback: use sha3_256 when eth_hash is unavailable.
        # NOTE: This is *not* EIP-55 compliant because Keccak-256 ≠ SHA3-256.
        # Checksum results will differ from the canonical spec in some cases.
        # Install ``eth-hash[pysha3]`` or ``eth-hash[pycryptodome]`` to enable
        # fully compliant checksumming; keeps the module importable in minimal
        # environments.
        import hashlib

        hash_hex = hashlib.sha3_256(hex_lower.encode()).hexdigest()

    result = "0x"
    for i, ch in enumerate(hex_lower):
        if ch in "0123456789":
            result += ch
        elif int(hash_hex[i], 16) >= 8:
            result += ch.upper()
        else:
            result += ch
    return result


# ---------------------------------------------------------------------------
# TokenUniverse
# ---------------------------------------------------------------------------


class TokenUniverse:
    """In-memory registry of :class:`~.types.TokenMeta` entries.

    Keyed by EIP-55 checksummed address.  All public methods normalise
    addresses before lookup / insertion so callers do not need to worry about
    case.

    The registry is initialised with *a copy* of ``tokens`` (or
    ``POLYGON_CORE_TOKENS`` when ``tokens`` is ``None``), so mutations of
    the original list do not affect the registry and vice-versa.
    """

    def __init__(self, tokens: Optional[List[TokenMeta]] = None) -> None:
        seed = tokens if tokens is not None else POLYGON_CORE_TOKENS
        self._registry: Dict[str, TokenMeta] = {}
        for token in seed:
            self._insert(token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise(self, address: str) -> str:
        """Normalise ``address`` to EIP-55 checksum form."""
        return _checksum(address)

    def _insert(self, token: TokenMeta) -> None:
        key = self._normalise(token.address)
        # Rebuild with the canonical address to ensure consistency.
        self._registry[key] = TokenMeta(
            address=key,
            decimals=token.decimals,
            symbol=token.symbol,
            chain_id=token.chain_id,
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, token: TokenMeta) -> None:
        """Add or overwrite a token entry.

        If a token with the same address already exists it is replaced.
        """
        self._insert(token)

    def remove(self, address: str) -> bool:
        """Remove the token at ``address``.

        Returns ``True`` when a token was removed, ``False`` when the address
        was not present.
        """
        key = self._normalise(address)
        if key in self._registry:
            del self._registry[key]
            return True
        return False

    def get(self, address: str) -> Optional[TokenMeta]:
        """Return the :class:`~.types.TokenMeta` for ``address``, or ``None``."""
        key = self._normalise(address)
        return self._registry.get(key)

    def contains(self, address: str) -> bool:
        """Return ``True`` when ``address`` is registered."""
        key = self._normalise(address)
        return key in self._registry

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------

    def all(self) -> List[TokenMeta]:
        """Return all registered tokens as an ordered list (by address)."""
        return sorted(self._registry.values(), key=lambda t: t.address)

    def addresses(self) -> List[str]:
        """Return all registered addresses (EIP-55, sorted)."""
        return sorted(self._registry.keys())

    def size(self) -> int:
        """Return the number of registered tokens."""
        return len(self._registry)

    def merge(self, other: "TokenUniverse") -> None:
        """Insert all tokens from ``other`` that are **not** already present.

        Existing entries are not overwritten; use :meth:`add` when you need
        to replace a token.
        """
        for token in other.all():
            if not self.contains(token.address):
                self._insert(token)

    def filter_by_chain(self, chain_id: int) -> List[TokenMeta]:
        """Return tokens whose ``chain_id`` matches ``chain_id``."""
        return [t for t in self._registry.values() if t.chain_id == chain_id]

    # ------------------------------------------------------------------
    # Iteration protocol
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[TokenMeta]:
        return iter(self.all())

    def __len__(self) -> int:
        return self.size()

    def __contains__(self, address: object) -> bool:
        if not isinstance(address, str):
            return False
        try:
            return self.contains(address)
        except ValueError:
            return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"TokenUniverse(size={self.size()})"
