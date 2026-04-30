"""Verified network constants for Polygon mainnet DeFi protocol integrations.

These are **immutable on-chain protocol addresses and fee tiers** — not live
data.  They encode the deployed addresses of DEX factories, vaults, and
well-known liquidity pools that are part of the Polygon ecosystem's permanent
infrastructure.  They are NOT the same as token prices, pool reserves, or any
state that changes block-to-block.

Labelling convention
--------------------
Every constant is prefixed with the chain name (``POLYGON_``) so that
multi-chain extensions can add ``ETHEREUM_``, ``BASE_``, etc. without
collision.  Constants are ``UPPER_SNAKE_CASE`` to distinguish them from
module-level configuration variables in other files.

What belongs here
-----------------
* DEX factory / router contract addresses.
* Vault / registry contract addresses.
* Canonical per-DEX fee tiers (fixed by protocol at deploy time).
* Addresses of specific pools that cannot be discovered via factory calls
  (e.g. Curve meta-pools registered separately from the main factory).
* The zero-address sentinel used in factory ``getPool`` / ``getPair`` calls.

What does NOT belong here
-------------------------
* Token addresses — those are part of the live token universe and are
  managed by :mod:`apex_omega_core.core.token_universe`.
* Token prices — those are live on-chain data.
* Pool reserves / TVL — those are live on-chain data.
* Gas price estimates — those are live via :mod:`apex_omega_core.core.mev_gas_oracle`.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Sentinel / helpers
# ---------------------------------------------------------------------------

#: The zero-address returned by factory ``getPool`` / ``getPair`` when a pool
#: does not exist.  Must be compared lower-case.
NULL_ADDRESS = "0x0000000000000000000000000000000000000000"

# ---------------------------------------------------------------------------
# Uniswap V3 — Polygon mainnet
# ---------------------------------------------------------------------------

#: Uniswap V3 factory (same bytecode as Ethereum, re-deployed on Polygon).
POLYGON_UNIV3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"

#: Uniswap V3 fee tiers in *fee units* (hundredths of a bip, i.e. 1 = 0.0001 %).
#: Fee as a decimal = fee_tier / 1_000_000.
POLYGON_UNIV3_FEE_TIERS: List[int] = [100, 500, 3000, 10000]

#: Uniswap V3 QuoterV2 (for off-chain simulation — NOT used for on-chain arb execution).
POLYGON_UNIV3_QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# ---------------------------------------------------------------------------
# QuickSwap V2 (Uniswap V2 fork) — Polygon mainnet
# ---------------------------------------------------------------------------

#: QuickSwap V2 factory — canonical constant since 2021 mainnet deployment.
POLYGON_QSV2_FACTORY = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"

#: QuickSwap V2 fixed fee (300 bps = 0.30 %).
POLYGON_QSV2_FEE: float = 0.003

# ---------------------------------------------------------------------------
# SushiSwap V2 — Polygon mainnet
# ---------------------------------------------------------------------------

#: SushiSwap V2 factory (UniV2-style).
POLYGON_SUSHI_V2_FACTORY = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"

#: SushiSwap fixed fee (300 bps).
POLYGON_SUSHI_V2_FEE: float = 0.003

# ---------------------------------------------------------------------------
# Balancer V2 — same vault address on all EVM chains
# ---------------------------------------------------------------------------

#: Balancer V2 vault — unified entry point for all pool interactions.
#: This address is canonical across Ethereum, Polygon, Arbitrum, etc.
POLYGON_BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

#: Whitelist of Balancer V2 50/50 weighted pools on Polygon to scan.
#: Format: (poolId, fee_decimal).  Add verified pool IDs here;
#: the discovery pipeline tolerates an empty list.
POLYGON_BALANCER_W50_POOLS: List[Tuple[str, float]] = []

# ---------------------------------------------------------------------------
# Curve Finance — Polygon mainnet
# ---------------------------------------------------------------------------

#: Curve am3CRV 3-coin StableSwap pool (DAI / USDCe / USDT).
#: This pool is registered outside the Curve factory and must be addressed
#: directly.  Coins must stay in declaration order (index-sensitive).
POLYGON_CURVE_AM3CRV: Dict = {
    "address": "0x445FE580eF8d70FF569aB36e80c647af338db351",
    "coins":   ["DAI", "USDCe", "USDT"],
}

# ---------------------------------------------------------------------------
# Chain identifiers
# ---------------------------------------------------------------------------

#: Polygon EVM chain ID.
POLYGON_CHAIN_ID: int = 137

# ---------------------------------------------------------------------------
# Convenience re-exports used by scanner modules
# ---------------------------------------------------------------------------

#: Alias for convenience in single-chain scanner code (``from network_constants import *``).
UNIV3_FACTORY   = POLYGON_UNIV3_FACTORY
QSV2_FACTORY    = POLYGON_QSV2_FACTORY
SUSHI_FACTORY   = POLYGON_SUSHI_V2_FACTORY
BALANCER_VAULT  = POLYGON_BALANCER_VAULT
CURVE_AM3CRV    = POLYGON_CURVE_AM3CRV
V3_FEE_TIERS    = POLYGON_UNIV3_FEE_TIERS
