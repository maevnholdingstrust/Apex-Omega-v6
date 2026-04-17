"""
MEV Gas Oracle — EIP-1559 fee-history polling, logistic P(fill) model,
and tip optimisation for the P_net × P(fill) > 0 guardrail.

Components
----------
GasPriceSnapshot   – lightweight value object holding fee-history statistics
FeeHistory         – raw eth_feeHistory response container
GasOracle          – fetches live EIP-1559 data from the RPC node
PFillEstimator     – logistic model: P(fill | tip) = σ((tip − μ) / σ_slope)
TipOptimizer       – grid-search that maximises E[profit] = P(fill) × (P_net − gas_cost)
"""

from __future__ import annotations

import importlib
import logging
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from web3 import Web3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Rust acceleration
# ---------------------------------------------------------------------------
try:
    _rust = importlib.import_module("apex_omega_core_rust")
    _rust_p_fill: Optional[object] = getattr(_rust, "p_fill_logistic", None)
    _rust_optimal_tip: Optional[object] = getattr(_rust, "optimal_tip_gwei", None)
except Exception:
    _rust = None
    _rust_p_fill = None
    _rust_optimal_tip = None

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class FeeHistory:
    """Raw output of eth_feeHistory converted to Python primitives."""
    base_fee_per_gas: List[int]        # wei; length = blocks + 1 (includes next block)
    reward_percentiles: List[List[int]]  # wei; one inner list per block
    gas_used_ratio: List[float]
    oldest_block: int


@dataclass
class GasPriceSnapshot:
    """Distilled fee-history summary used by PFillEstimator and TipOptimizer."""
    base_fee_gwei: float
    tip_p25_gwei: float
    tip_p50_gwei: float
    tip_p75_gwei: float
    tip_p90_gwei: float
    gas_used_ratio_avg: float


# ---------------------------------------------------------------------------
# Gas Oracle
# ---------------------------------------------------------------------------

class GasOracle:
    """
    Live EIP-1559 gas oracle.

    Fetches ``eth_feeHistory`` for the last ``BLOCKS`` blocks and distils the
    result into a :class:`GasPriceSnapshot` that downstream components use to
    estimate P(fill) and compute optimal tips.

    Falls back to ``eth_gasPrice`` when the node does not support
    ``eth_feeHistory`` (e.g. older RPC endpoints).
    """

    BLOCKS: int = 20

    # Private list used throughout the class; avoids mutable class-level default.
    _PERCENTILES: List[int] = [25, 50, 75, 90]

    def __init__(self, rpc_url: Optional[str] = None, w3: Optional[Web3] = None):
        self.rpc_url = rpc_url or os.getenv("APEX_RPC_URL", "https://polygon-rpc.com/")
        self.w3 = w3 or Web3(Web3.HTTPProvider(self.rpc_url))
        self._snapshot: Optional[GasPriceSnapshot] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_fee_history(self) -> FeeHistory:
        """Call ``eth_feeHistory`` and return structured data."""
        raw = self.w3.eth.fee_history(self.BLOCKS, "latest", self._PERCENTILES)
        return FeeHistory(
            base_fee_per_gas=[int(x) for x in raw.baseFeePerGas],
            reward_percentiles=[
                [int(r) for r in block_rewards]
                for block_rewards in (raw.reward or [])
            ],
            gas_used_ratio=list(raw.gasUsedRatio),
            oldest_block=int(raw.oldestBlock),
        )

    def get_snapshot(self, force: bool = False) -> GasPriceSnapshot:
        """Return a (possibly cached) :class:`GasPriceSnapshot`.

        Pass ``force=True`` to skip the cache and re-fetch from the RPC node.
        The result is cached so that multiple callers within the same scan
        cycle share one RPC round-trip.
        """
        if self._snapshot is not None and not force:
            return self._snapshot

        try:
            history = self.fetch_fee_history()
            self._snapshot = self._build_snapshot(history)
        except Exception as exc:
            logger.warning(
                "eth_feeHistory failed (%s); falling back to eth_gasPrice", exc
            )
            self._snapshot = self._fallback_snapshot()

        return self._snapshot

    def invalidate(self) -> None:
        """Clear the cached snapshot (call between scan cycles)."""
        self._snapshot = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_snapshot(self, history: FeeHistory) -> GasPriceSnapshot:
        """Convert raw ``FeeHistory`` into a ``GasPriceSnapshot``."""
        # The *last* entry in baseFeePerGas is the projected fee for the *next* block.
        base_fee_gwei = history.base_fee_per_gas[-1] / 1e9

        # Collect per-percentile tip lists across all fetched blocks.
        tip_by_pct: List[List[float]] = [[] for _ in self._PERCENTILES]
        for block_rewards in history.reward_percentiles:
            for i, reward in enumerate(block_rewards):
                if i < len(self._PERCENTILES):
                    tip_by_pct[i].append(reward / 1e9)

        def _median(vals: List[float]) -> float:
            if not vals:
                return 0.0
            vals = sorted(vals)
            mid = len(vals) // 2
            return (vals[mid - 1] + vals[mid]) / 2 if len(vals) % 2 == 0 else vals[mid]

        gas_used_avg = (
            sum(history.gas_used_ratio) / len(history.gas_used_ratio)
            if history.gas_used_ratio
            else 0.5
        )

        # Fallback defaults (Gwei) when the node returns empty reward lists.
        defaults = [0.5, 1.0, 2.0, 5.0]
        tips = [
            _median(tip_by_pct[i]) if tip_by_pct[i] else defaults[i]
            for i in range(len(self._PERCENTILES))
        ]

        return GasPriceSnapshot(
            base_fee_gwei=base_fee_gwei,
            tip_p25_gwei=tips[0],
            tip_p50_gwei=tips[1],
            tip_p75_gwei=tips[2],
            tip_p90_gwei=tips[3],
            gas_used_ratio_avg=gas_used_avg,
        )

    def _fallback_snapshot(self) -> GasPriceSnapshot:
        """Build a conservative snapshot from legacy ``eth_gasPrice``."""
        try:
            gas_price_gwei = self.w3.eth.gas_price / 1e9
        except Exception:
            gas_price_gwei = 100.0  # Polygon worst-case default

        return GasPriceSnapshot(
            base_fee_gwei=gas_price_gwei * 0.85,
            tip_p25_gwei=max(gas_price_gwei * 0.03, 0.5),
            tip_p50_gwei=max(gas_price_gwei * 0.05, 1.0),
            tip_p75_gwei=max(gas_price_gwei * 0.08, 2.0),
            tip_p90_gwei=max(gas_price_gwei * 0.12, 5.0),
            gas_used_ratio_avg=0.5,
        )


# ---------------------------------------------------------------------------
# P(fill) Estimator
# ---------------------------------------------------------------------------

class PFillEstimator:
    """
    Logistic model for the probability of transaction inclusion in the next block.

    .. math::

        P(fill \\mid tip) = \\frac{1}{1 + e^{-(tip - \\mu) / \\sigma}}

    Calibration
    -----------
    * ``μ``  = ``tip_p50_gwei``  — 50 % of builders accept at this level → P(fill) = 0.50
    * ``σ``  = ``(tip_p75 - tip_p25) / 4``  — derived so that the logistic
      crosses ≈ 0.88 at p75 and ≈ 0.12 at p25 (2σ interval from μ).

    The Rust kernel (:func:`apex_omega_core_rust.p_fill_logistic`) is used when
    available; otherwise the computation falls back to pure Python.
    """

    def __init__(self, snapshot: GasPriceSnapshot):
        self.mu = snapshot.tip_p50_gwei
        spread = snapshot.tip_p75_gwei - snapshot.tip_p25_gwei
        # sigma so that logistic(p75) ≈ 0.88 → (p75 - μ) / σ ≈ 2
        self.sigma = max(spread / 4.0, 0.05)

    def estimate(self, tip_gwei: float) -> float:
        """Return P(fill) ∈ [0.0, 1.0] for the given ``maxPriorityFeePerGas`` tip (Gwei)."""
        if _rust_p_fill is not None:
            return float(_rust_p_fill(float(tip_gwei), float(self.mu), float(self.sigma)))
        return self._python_logistic(tip_gwei)

    def _python_logistic(self, tip_gwei: float) -> float:
        safe_sigma = max(self.sigma, 1e-9)
        exponent = -(tip_gwei - self.mu) / safe_sigma
        try:
            return 1.0 / (1.0 + math.exp(exponent))
        except OverflowError:
            return 0.0 if exponent > 0 else 1.0


# ---------------------------------------------------------------------------
# Tip Optimizer
# ---------------------------------------------------------------------------

class TipOptimizer:
    """
    Maximise expected profit over the tip dimension.

    .. math::

        \\text{E}[\\text{profit}] = P(fill \\mid tip) \\times (P_{net} - \\text{gas\\_cost}(tip))

    where

    .. math::

        \\text{gas\\_cost}(tip) = \\text{gas\\_units} \\times (\\text{base\\_fee} + tip) \\times 10^{-9}
                                   \\times \\text{native\\_price\\_usd}

    A grid search over ``[0, max_tip_gwei]`` is performed with ``GRID_STEPS``
    intervals.  The Rust kernel (:func:`apex_omega_core_rust.optimal_tip_gwei`)
    is used when available for a faster, identical result.

    Attributes
    ----------
    MATIC_PRICE_USD : float
        Conservative MATIC price used for gas cost conversion on Polygon.
    ETH_PRICE_USD : float
        Conservative ETH price used on Ethereum / other EVM chains.
    GRID_STEPS : int
        Number of grid intervals for the tip search.
    """

    MATIC_PRICE_USD: float = 0.85
    ETH_PRICE_USD: float = 3500.0
    GRID_STEPS: int = 200

    def __init__(
        self,
        snapshot: GasPriceSnapshot,
        gas_units: int = 350_000,
        chain: str = "polygon",
    ):
        self.snapshot = snapshot
        self.p_fill = PFillEstimator(snapshot)
        self.gas_units = gas_units
        self.native_price_usd = (
            self.MATIC_PRICE_USD if chain == "polygon" else self.ETH_PRICE_USD
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def gas_cost_usd(self, tip_gwei: float) -> float:
        """Total gas cost in USD at the given tip."""
        total_gwei = self.snapshot.base_fee_gwei + tip_gwei
        native_used = self.gas_units * total_gwei * 1e-9
        return native_used * self.native_price_usd

    def expected_profit(self, p_net_usd: float, tip_gwei: float) -> float:
        """``E[profit]`` for a given net profit and tip level."""
        net = p_net_usd - self.gas_cost_usd(tip_gwei)
        if net <= 0.0:
            return 0.0
        return self.p_fill.estimate(tip_gwei) * net

    def optimal_tip(self, p_net_usd: float) -> float:
        """Return the tip (Gwei) that maximises ``E[profit]`` for the given ``P_net``."""
        max_tip = self.snapshot.tip_p90_gwei * 3.0
        if _rust_optimal_tip is not None:
            best_tip, _best_ep, _best_pf = _rust_optimal_tip(
                float(p_net_usd),
                float(self.snapshot.base_fee_gwei),
                float(self.p_fill.mu),
                float(self.p_fill.sigma),
                int(self.gas_units),
                float(self.native_price_usd),
                float(max_tip),
                int(self.GRID_STEPS),
            )
            return float(best_tip)
        return self._grid_search(p_net_usd, max_tip)

    def build_eip1559_params(self, p_net_usd: float) -> Dict[str, object]:
        """
        Return a dict with EIP-1559 gas parameters and profitability metadata.

        Keys
        ----
        maxPriorityFeePerGas : int  (Wei)
        maxFeePerGas         : int  (Wei)  — base_fee × 2 + tip for surge headroom
        tip_gwei             : float
        base_fee_gwei        : float
        p_fill               : float  — P(fill) at the chosen tip
        expected_profit_usd  : float
        gas_cost_usd         : float
        """
        tip_gwei = self.optimal_tip(p_net_usd)
        base_fee_gwei = self.snapshot.base_fee_gwei
        # 2× base_fee headroom absorbs EIP-1559 surges within the same block window.
        max_fee_gwei = base_fee_gwei * 2.0 + tip_gwei

        return {
            "maxPriorityFeePerGas": int(tip_gwei * 1e9),
            "maxFeePerGas": int(max_fee_gwei * 1e9),
            "tip_gwei": tip_gwei,
            "base_fee_gwei": base_fee_gwei,
            "p_fill": self.p_fill.estimate(tip_gwei),
            "expected_profit_usd": self.expected_profit(p_net_usd, tip_gwei),
            "gas_cost_usd": self.gas_cost_usd(tip_gwei),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _grid_search(self, p_net_usd: float, max_tip: float) -> float:
        best_tip = 0.0
        best_ep = float("-inf")
        step = max_tip / self.GRID_STEPS

        tip = 0.0
        while tip <= max_tip + 1e-9:
            ep = self.expected_profit(p_net_usd, tip)
            if ep > best_ep:
                best_ep = ep
                best_tip = tip
            tip += step

        return best_tip
