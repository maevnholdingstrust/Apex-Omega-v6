"""Token universe: verified seed tokens and optional live discovery for Polygon DEX scans.

Features
--------
* Hard-coded seed tokens — Polygon mainnet verified addresses.  These are the
  same tokens used in :mod:`dry_run` and serve as the authoritative fallback.
* Environment overlay — set ``APEX_TOKEN_UNIVERSE=WMATIC,USDC,WETH`` to
  restrict the universe to a comma-separated subset, or
  ``APEX_TOKEN_UNIVERSE_EXTRA=FOO:0x…:18,BAR:0x…:6`` to append extra tokens
  without losing the seed list.
* Live discovery adapters — pluggable, opt-in.  Any adapter that throws is
  silently swallowed; the seed tokens are always available.

Design guarantees
-----------------
* ``TokenUniverse.get_tokens()`` **never raises** regardless of network state.
* External API failures are logged at WARNING level and silently bypassed.
* No adapter is called unless the caller explicitly passes one.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed token registry — Polygon mainnet (checksummed addresses).
# Format: symbol → (checksum_address, decimals).
# Mirrors _TOKENS in dry_run.py; update both when adding new tokens.
# ---------------------------------------------------------------------------

SEED_TOKENS: Dict[str, Tuple[str, int]] = {
    # Stablecoins
    "USDCe":   ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),   # bridged USDC
    "USDC":    ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),   # native USDC
    "USDT":    ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":     ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
    "FRAX":    ("0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89", 18),
    "MAI":     ("0xa3Fa99A148fA48D14Ed51d610c367C61876997F1", 18),
    "TUSD":    ("0x2e1AD108fF1D8C782fcBbB89AAd783aC49586756", 18),
    # Majors / wrapped
    "WMATIC":  ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    "WETH":    ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WBTC":    ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
    # MATIC liquid staking derivatives
    "stMATIC": ("0x3A58a54C066FdC0f2D55FC9C89F0415C92eBf3C4", 18),
    "MaticX":  ("0xfa68FB4628DFF1028CFEc22b4162FCcd0d45efb6", 18),
    # ETH liquid staking derivatives
    "wstETH":  ("0x03b54A6e9a984069379fae1a4fC4dBAE93B3bCCD", 18),
    # Blue-chip DeFi
    "LINK":    ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
    "AAVE":    ("0xD6DF932A45108d2930D8EB3375F7f50AdDA1a5A4", 18),
    "CRV":     ("0x172370d5Cd63279eFa6d502DAB29171933a610AF", 18),
    "BAL":     ("0x9a71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3", 18),
    "SUSHI":   ("0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a", 18),
    "UNI":     ("0xb33EaAd8d922B1083446DC23f610c2567fB5180f", 18),
    "COMP":    ("0x8505b9d2254A7Ae468c0E9dd10Ccea3A837aef5c", 18),
    "MKR":     ("0x6f7C932e7684666C9fd1d44527765433e01fF61d", 18),
    "SNX":     ("0x50B728D8D964fd00C2d0AAD81718b71311feF68a", 18),
    "GHST":    ("0x385Eeac5cB85A38A9a07A70c73e0a3271CfB54A7", 18),
    "QUICK":   ("0xB5C064F955D8e7F38fE0460C556a72987494eE17", 18),
    "FXS":     ("0x1a3acf6D19267E2d3e7f898f42803e90C9219062", 18),
    "DPI":     ("0x85955046DF4668e1DD369D2DE9f3AEFC9cD8DA0E", 18),
    # Gaming / metaverse
    "SAND":    ("0xBbba073C31bF03b8ACf7c28EF0738DeCF3695683", 18),
    "MANA":    ("0xA1c57f48F0Deb89f569dFbE6E2B7f46D33606fD4", 18),
}

# Type alias for a discovery adapter.
# A discovery adapter is a callable that takes no arguments and returns a
# dict in the same format as SEED_TOKENS: {symbol: (address, decimals)}.
# It may raise any exception; the caller wraps it safely.
DiscoveryAdapter = Callable[[], Dict[str, Tuple[str, int]]]


# ---------------------------------------------------------------------------
# TokenUniverse
# ---------------------------------------------------------------------------

class TokenUniverse:
    """Resolved token universe for a single scan session.

    Parameters
    ----------
    env_filter_var
        Name of the environment variable whose comma-separated value restricts
        the universe to a named subset of SEED_TOKENS (e.g. ``"WMATIC,USDC"``).
        Unknown symbols are silently ignored.  Default: ``"APEX_TOKEN_UNIVERSE"``.
    env_extra_var
        Name of the environment variable for extra tokens not in SEED_TOKENS.
        Format: ``"SYM:0xADDR:DECIMALS,SYM2:0xADDR2:DECIMALS2"``.
        Parse errors are logged and skipped.  Default: ``"APEX_TOKEN_UNIVERSE_EXTRA"``.
    adapters
        Optional list of live-discovery callables.  Each is invoked once and
        merged (symbol-level, with seed data taking priority).  Any exception
        from an adapter is caught and logged; the seed tokens are unaffected.
    """

    def __init__(
        self,
        env_filter_var: str = "APEX_TOKEN_UNIVERSE",
        env_extra_var: str = "APEX_TOKEN_UNIVERSE_EXTRA",
        adapters: Optional[List[DiscoveryAdapter]] = None,
    ) -> None:
        self._env_filter_var = env_filter_var
        self._env_extra_var = env_extra_var
        self._adapters: List[DiscoveryAdapter] = adapters or []
        self._resolved: Optional[Dict[str, Tuple[str, int]]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tokens(self) -> Dict[str, Tuple[str, int]]:
        """Return the resolved token universe as ``{symbol: (address, decimals)}``.

        Results are cached after the first call within an instance lifetime.
        Never raises — any external API failure falls back to seed data.
        """
        if self._resolved is None:
            self._resolved = self._build()
        return self._resolved

    def get_pairs(self) -> List[Tuple[str, str]]:
        """Return all unordered symbol pairs from the resolved universe."""
        symbols = sorted(self.get_tokens().keys())
        return [
            (a, b)
            for i, a in enumerate(symbols)
            for b in symbols[i + 1:]
        ]

    def invalidate(self) -> None:
        """Clear the cached result so the next call rebuilds from scratch."""
        self._resolved = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build(self) -> Dict[str, Tuple[str, int]]:
        """Build the resolved token dict.  Always returns a non-empty dict."""
        merged: Dict[str, Tuple[str, int]] = {}

        # 1. Run optional live-discovery adapters (merge before seed so seed wins)
        for adapter in self._adapters:
            try:
                live = adapter()
                if isinstance(live, dict):
                    for sym, (addr, dec) in live.items():
                        merged.setdefault(sym, (addr, dec))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "TokenUniverse: live discovery adapter %s failed (%s): %s",
                    getattr(adapter, "__name__", repr(adapter)),
                    type(exc).__name__,
                    exc,
                )

        # 2. Layer seed tokens (seed always wins over adapter data)
        merged.update(SEED_TOKENS)

        # 3. Apply env extras (appended, do not override seed)
        extra_raw = os.getenv(self._env_extra_var, "").strip()
        if extra_raw:
            for entry in extra_raw.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                parts = entry.split(":")
                if len(parts) != 3:
                    logger.warning(
                        "TokenUniverse: skipping malformed extra token entry %r "
                        "(expected SYM:0xADDR:DECIMALS)",
                        entry,
                    )
                    continue
                sym, addr, dec_str = parts
                sym = sym.strip()
                try:
                    dec = int(dec_str.strip())
                except ValueError:
                    logger.warning(
                        "TokenUniverse: non-integer decimals in entry %r", entry
                    )
                    continue
                merged.setdefault(sym, (addr.strip(), dec))

        # 4. Apply env filter (restrict to named subset)
        filter_raw = os.getenv(self._env_filter_var, "").strip()
        if filter_raw:
            allowed = {s.strip() for s in filter_raw.split(",") if s.strip()}
            unknown = allowed - merged.keys()
            if unknown:
                logger.warning(
                    "TokenUniverse: filter contains unknown symbols: %s — ignoring",
                    ", ".join(sorted(unknown)),
                )
            filtered = {sym: v for sym, v in merged.items() if sym in allowed}
            if filtered:
                merged = filtered
            else:
                logger.warning(
                    "TokenUniverse: env filter %r resolved to empty set — "
                    "using full seed universe",
                    filter_raw,
                )

        if not merged:
            logger.error(
                "TokenUniverse: resolved universe is empty — falling back to SEED_TOKENS"
            )
            merged = dict(SEED_TOKENS)

        return merged


# ---------------------------------------------------------------------------
# Module-level convenience (stateless, uses SEED_TOKENS directly)
# ---------------------------------------------------------------------------

def get_seed_tokens() -> Dict[str, Tuple[str, int]]:
    """Return the immutable seed token dict (no env overlay, no adapters)."""
    return dict(SEED_TOKENS)


def get_seed_pairs() -> List[Tuple[str, str]]:
    """Return all unordered seed-token symbol pairs."""
    symbols = sorted(SEED_TOKENS.keys())
    return [
        (a, b)
        for i, a in enumerate(symbols)
        for b in symbols[i + 1:]
    ]
