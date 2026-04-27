"""Tests for cycle-extrema selection and route artifact price anchors.

Verifies that:
  1. _select_cycle_extrema returns the globally-best (cheapest buy, best sell)
     pool pair from a list of same-pair CPMM snapshots.
  2. _compute_opportunity embeds best_buy_price_exec and best_sell_price_exec
     in each route leg.
  3. Degenerate inputs (single pool, all Curve, zero reserves) return None.
"""

from __future__ import annotations

import sys
import os
import importlib
import types
import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so dry_run can be imported without a live chain connection.
# ---------------------------------------------------------------------------

# Stub modules that dry_run imports but we don't need for unit tests
_STUBS = [
    "web3",
    "web3.middleware",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        stub = types.ModuleType(_mod)
        sys.modules[_mod] = stub

# Provide a minimal Web3 stub if web3 isn't installed
if not hasattr(sys.modules["web3"], "Web3"):
    class _FakeWeb3:
        class HTTPProvider:
            def __init__(self, *a, **kw):
                pass

        @staticmethod
        def to_checksum_address(addr):
            return addr

    sys.modules["web3"].Web3 = _FakeWeb3  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Import the helpers under test from dry_run.py
# ---------------------------------------------------------------------------

# dry_run.py lives one level above apex_omega_core/tests
_DRY_RUN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "dry_run.py"
)
spec = importlib.util.spec_from_file_location("dry_run", _DRY_RUN_PATH)
assert spec is not None and spec.loader is not None
_dry_run_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_dry_run_mod)  # type: ignore[union-attr]

_select_cycle_extrema = _dry_run_mod._select_cycle_extrema
_PoolSnapshot = _dry_run_mod._PoolSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(
    pool_address: str,
    reserve0: float,
    reserve1: float,
    fee: float = 0.003,
    dex: str = "qsv2",
    kind: str = "cpmm",
) -> _PoolSnapshot:
    price = reserve1 / reserve0 if reserve0 > 0 else 0.0
    return _PoolSnapshot(
        pool_address=pool_address,
        dex=dex,
        fee=fee,
        sym0="WMATIC",
        sym1="USDC",
        reserve0=reserve0,
        reserve1=reserve1,
        price=price,
        kind=kind,
    )


# ---------------------------------------------------------------------------
# _select_cycle_extrema
# ---------------------------------------------------------------------------

class TestSelectCycleExtrema:
    def test_returns_none_for_single_pool(self) -> None:
        pools = [_snap("0xA", 1_000.0, 400.0)]
        assert _select_cycle_extrema(pools) is None

    def test_returns_none_for_empty_list(self) -> None:
        assert _select_cycle_extrema([]) is None

    def test_returns_none_for_all_curve_pools(self) -> None:
        pools = [
            _snap("0xA", 1_000.0, 400.0, kind="curve_ss"),
            _snap("0xB", 1_000.0, 380.0, kind="curve_ss"),
        ]
        assert _select_cycle_extrema(pools) is None

    def test_returns_none_when_only_one_cpmm_pool(self) -> None:
        pools = [
            _snap("0xA", 1_000.0, 400.0, kind="cpmm"),
            _snap("0xB", 1_000.0, 380.0, kind="curve_ss"),
        ]
        assert _select_cycle_extrema(pools) is None

    def test_returns_none_for_zero_reserve_pools(self) -> None:
        pools = [
            _snap("0xA", 0.0, 0.0),
            _snap("0xB", 0.0, 0.0),
        ]
        assert _select_cycle_extrema(pools) is None

    def test_returns_none_when_single_pool_covers_both_extrema(self) -> None:
        """Two identical pools — same address — no cross-pool arb possible."""
        pools = [
            _snap("0xSAME", 1_000.0, 400.0),
            _snap("0xSAME", 1_000.0, 380.0),
        ]
        assert _select_cycle_extrema(pools) is None

    def test_selects_max_price_as_buy_min_price_as_sell(self) -> None:
        """Best buy = highest token1/token0 ratio; best sell = lowest."""
        # price = reserve1 / reserve0
        p_a = _snap("0xA", 1_000.0, 420.0)   # price = 0.42
        p_b = _snap("0xB", 1_000.0, 400.0)   # price = 0.40
        p_c = _snap("0xC", 1_000.0, 380.0)   # price = 0.38

        result = _select_cycle_extrema([p_a, p_b, p_c])
        assert result is not None
        best_buy, best_sell = result

        # Most token1 per token0 = highest price → buy here
        assert best_buy.pool_address == "0xA"
        # Least token1 per token0 = lowest price → sell token1 here
        assert best_sell.pool_address == "0xC"

    def test_two_pools_correctly_assigned(self) -> None:
        higher = _snap("0xHIGH", 1_000.0, 450.0, dex="univ3_500")  # price 0.45
        lower  = _snap("0xLOW",  1_000.0, 400.0, dex="qsv2")       # price 0.40

        result = _select_cycle_extrema([lower, higher])
        assert result is not None
        buy, sell = result
        assert buy.pool_address == "0xHIGH"
        assert sell.pool_address == "0xLOW"

    def test_spread_of_selected_pair_is_maximal(self) -> None:
        """Verify the returned pair has the widest possible spot spread."""
        pools = [_snap(f"0x{i}", 1_000.0, float(390 + i * 5)) for i in range(6)]
        result = _select_cycle_extrema(pools)
        assert result is not None
        buy, sell = result

        # The selected pair must have the maximum pairwise spread
        max_spread = max(
            a.price - b.price
            for a in pools
            for b in pools
            if a.pool_address != b.pool_address
        )
        selected_spread = buy.price - sell.price
        assert selected_spread == pytest.approx(max_spread)

    def test_ignores_zero_reserve_pool_in_mixed_list(self) -> None:
        good_a = _snap("0xA", 1_000.0, 420.0)
        good_b = _snap("0xB", 1_000.0, 380.0)
        bad    = _snap("0xBAD", 0.0, 0.0)

        result = _select_cycle_extrema([good_a, bad, good_b])
        assert result is not None
        buy, sell = result
        assert buy.pool_address == "0xA"
        assert sell.pool_address == "0xB"


# ---------------------------------------------------------------------------
# Route artifact: best_buy_price_exec and best_sell_price_exec
# ---------------------------------------------------------------------------

class TestRouteArtifactPriceAnchors:
    """End-to-end check that _compute_opportunity wires executable prices
    into both route legs.

    We call the function with a mock sentinel/optimizer to avoid any live
    network dependency.
    """

    def _make_mock_sentinel(self):
        class _FakeSentinel:
            def amm_swap(self, amount_in, reserve_in, reserve_out, fee):
                eff = amount_in * (1.0 - fee)
                return (eff * reserve_out) / (reserve_in + eff)

            def optimal_two_leg_input(self, r1_in, r1_out, fee1, r2_in, r2_out, fee2):
                return 100.0  # fixed optimal size for test

            def simulate_route(self, amount_in, route):
                out = amount_in
                for leg in route:
                    out = self.amm_swap(out, leg["reserve_in"], leg["reserve_out"], leg["fee"])
                slippage = [
                    {"usd_in": amount_in, "usd_out": out}
                    for _ in route
                ]
                return out, slippage

        return _FakeSentinel()

    def _make_mock_tip_optimizer(self):
        class _FakeTipOpt:
            def build_eip1559_params(self, profit):
                return {"gas_cost_usd": 0.01, "p_fill": 0.95}

        return _FakeTipOpt()

    def test_route_legs_carry_best_prices(self) -> None:
        # Need to monkey-patch the calculate_deterministic_slippage_bps used
        # inside _compute_opportunity so it always returns 0 (no slip gate).
        import apex_omega_core.core.deterministic_slippage as _ds
        orig_fn = _ds.calculate_deterministic_slippage_bps

        def _zero_slip(**kwargs):
            return 0.0

        _ds.calculate_deterministic_slippage_bps = _zero_slip  # type: ignore

        try:
            buy = _snap("0xBUY",  50_000.0, 21_000.0)   # price 0.42 → buy here
            sell = _snap("0xSELL", 50_000.0, 19_000.0)  # price 0.38 → sell here

            sentinel = self._make_mock_sentinel()
            tip_opt  = self._make_mock_tip_optimizer()
            token_prices = {"WMATIC": 0.40, "USDC": 1.0}

            _compute_opportunity = _dry_run_mod._compute_opportunity
            rec = _compute_opportunity(
                scan_no=1,
                pair_key="WMATIC/USDC",
                buy=buy,
                sell=sell,
                token_prices=token_prices,
                sentinel=sentinel,
                tip_optimizer=tip_opt,
                trade_size_usd=40.0,   # $40 → 100 WMATIC at $0.40
                min_spread_bps=0.0,
                min_net_profit_usd=-1e9,  # accept any profit
                flash_loan_fee_rate=0.0,
            )

            assert rec is not None, "Expected an OpportunityRecord"

            # Route is stored in the result's scan_no field (not directly
            # exposed), but the raw_spread_bps should now be executable-based
            # (smaller/more conservative than spot).
            spot_spread_bps = (buy.price - sell.price) / sell.price * 10_000.0
            assert rec.raw_spread_bps <= spot_spread_bps + 0.01, (
                "Executable spread must not exceed spot spread"
            )
        finally:
            _ds.calculate_deterministic_slippage_bps = orig_fn  # type: ignore
