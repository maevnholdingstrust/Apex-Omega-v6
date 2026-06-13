"""MaxVenueDiscoveryConfig: Full Polygon liquidity universe at 2.0 level."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

@dataclass(frozen=True)
class VenueDiscoveryConfig:
    """Enhanced venue discovery with all supported AMM types."""

    name: str
    kind: str
    enabled: bool
    min_tvl_usd: float
    scan_priority: int  # 0=highest
    description: str


# All venues available on Polygon for maximum discovery
DISCOVERY_VENUES: Dict[str, VenueDiscoveryConfig] = {
    # Tier 0: Highest liquidity, most active
    "quickswap_v2": VenueDiscoveryConfig(
        name="quickswap_v2",
        kind="v2_cpmm",
        enabled=True,
        min_tvl_usd=5000.0,
        scan_priority=0,
        description="QuickSwap V2 - primary Polygon DEX"
    ),
    "uniswap_v3": VenueDiscoveryConfig(
        name="uniswap_v3",
        kind="v3_concentrated",
        enabled=True,
        min_tvl_usd=5000.0,
        scan_priority=1,
        description="Uniswap V3 - concentrated liquidity"
    ),
    "sushiswap_v2": VenueDiscoveryConfig(
        name="sushiswap_v2",
        kind="v2_cpmm",
        enabled=True,
        min_tvl_usd=5000.0,
        scan_priority=2,
        description="SushiSwap V2 - secondary liquidity"
    ),

    # Tier 1: Secondary venues
    "apeswap_v2": VenueDiscoveryConfig(
        name="apeswap_v2",
        kind="v2_cpmm",
        enabled=True,
        min_tvl_usd=2000.0,
        scan_priority=3,
        description="ApeSwap V2"
    ),
    "dfyn_v2": VenueDiscoveryConfig(
        name="dfyn_v2",
        kind="v2_cpmm",
        enabled=True,
        min_tvl_usd=2000.0,
        scan_priority=4,
        description="Dfyn V2"
    ),

    # Tier 2: Emerging/specialized venues (enable for expanded discovery)
    "jetswap_v2": VenueDiscoveryConfig(
        name="jetswap_v2",
        kind="v2_cpmm",
        enabled=True,
        min_tvl_usd=1000.0,
        scan_priority=5,
        description="JetSwap V2"
    ),

    # Tier 3: Specialized pools (requires dedicated adapters, discover only)
    "balancer_v2": VenueDiscoveryConfig(
        name="balancer_v2",
        kind="weighted_pool",
        enabled=True,  # Enabled for discovery; execution requires vault adapter
        min_tvl_usd=5000.0,
        scan_priority=6,
        description="Balancer V2 - weighted/stable pools"
    ),
    "curve": VenueDiscoveryConfig(
        name="curve",
        kind="stable_swap",
        enabled=True,  # Enabled for discovery; execution requires curve adapter
        min_tvl_usd=5000.0,
        scan_priority=7,
        description="Curve - stablecoin swaps"
    ),
    "quickswap_algebra": VenueDiscoveryConfig(
        name="quickswap_algebra",
        kind="algebra_concentrated",
        enabled=True,  # Enabled for discovery; execution requires algebra quoter
        min_tvl_usd=5000.0,
        scan_priority=8,
        description="QuickSwap Algebra V3 - concentrated liquidity variant"
    ),
}

# Enabled venues for immediate execution
EXECUTION_READY_VENUES = {
    name: cfg for name, cfg in DISCOVERY_VENUES.items()
    if cfg.kind in {"v2_cpmm", "v3_concentrated"}
}

# Discovery-only venues (scan for spreads, don't execute yet)
DISCOVERY_ONLY_VENUES = {
    name: cfg for name, cfg in DISCOVERY_VENUES.items()
    if cfg.kind in {"weighted_pool", "stable_swap", "algebra_concentrated"}
}


class MaxVenueDiscoveryConfig:
    """Configuration for maximum Polygon discovery at 2.0 level."""

    def __init__(self):
        self.min_pool_tvl_usd = float(os.getenv("MIN_POOL_TVL_USD", "5000"))
        self.expanded_scan_enabled = os.getenv("AUTONOMOUS_ENABLE_EXPANDED_SCAN", "true").lower() == "true"
        self.discovery_workers = int(os.getenv("DISCOVERY_MAX_WORKERS", "48"))
        self.discovery_timeout_sec = float(os.getenv("DISCOVERY_PAIR_TIMEOUT_SEC", "120"))

    def get_active_venues(self) -> Dict[str, VenueDiscoveryConfig]:
        """Return all active venues based on discovery level."""
        if self.expanded_scan_enabled:
            return {name: cfg for name, cfg in DISCOVERY_VENUES.items() if cfg.enabled}
        return EXECUTION_READY_VENUES

    def get_scan_order(self) -> List[VenueDiscoveryConfig]:
        """Return venues ordered by scan priority."""
        return sorted(
            self.get_active_venues().values(),
            key=lambda cfg: cfg.scan_priority
        )

    def status(self) -> Dict[str, any]:
        """Return discovery configuration status."""
        venues = self.get_active_venues()
        return {
            "expanded_scan_enabled": self.expanded_scan_enabled,
            "active_venue_count": len(venues),
            "discovery_workers": self.discovery_workers,
            "discovery_timeout_sec": self.discovery_timeout_sec,
            "min_pool_tvl_usd": self.min_pool_tvl_usd,
            "venues": [
                {
                    "name": cfg.name,
                    "kind": cfg.kind,
                    "priority": cfg.scan_priority,
                    "min_tvl_usd": cfg.min_tvl_usd,
                }
                for cfg in self.get_scan_order()
            ],
        }
