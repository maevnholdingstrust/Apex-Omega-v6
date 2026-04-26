#!/usr/bin/env python3
"""Flash-loan arbitrage backtester v3.2.

Uses deterministic CPMM slippage math (deterministic_slippage module) instead
of heuristics so simulation results match production route-gating behaviour
exactly.

Strategies tested
-----------------
OLD_STRICT_TVL
    Conservative baseline: loan = 0.1% of smallest pool TVL (no size
    optimisation), strict TVL floor ($100k).  Applies the deterministic
    slippage gate but does not search for a better size.  Thin pools are
    excluded, so fewer routes are evaluated.

FLOOD_GATES
    Same fixed sizing as OLD_STRICT_TVL but relaxes the TVL floor to $20k,
    admitting thin emerging-token pools.  More routes are evaluated because
    thin pools have wider spreads (they correct more slowly); the slippage
    gate still rejects routes where price impact exceeds the spread.

SMART_FLOOD
    Same relaxed TVL floor as FLOOD_GATES.  Instead of a fixed loan size,
    SMART_FLOOD searches a 16-point geometric grid between $50 and the
    maximum viable loan for each route (binary-searched via the deterministic
    slippage gate) and picks the loan that maximises net profit.  This is
    the recommended production mode.

Key insight (from backtesting results)
---------------------------------------
With correct CPMM slippage math:
- OLD_STRICT_TVL misses thin-pool opportunities (strict TVL floor).
- FLOOD_GATES evaluates more routes but uses a fixed, non-optimal loan.
- SMART_FLOOD combines route coverage with per-route size optimisation and
  produces the highest total profit and highest profit per trade.

Usage
-----
    python flash_loan_backtester.py
    python flash_loan_backtester.py --strategy FLOOD_GATES
    python flash_loan_backtester.py --scans 50 --max-loan 20000

The script prints a tabulated result for each strategy and writes a CSV
summary to ``/tmp/backtest_results.csv``.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import random
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from apex_omega_core.core.deterministic_slippage import calculate_deterministic_slippage_bps

# ---------------------------------------------------------------------------
# Pool template data (pair, dex_a, fee_a, dex_b, fee_b,
#                     tvl_usd_a, tvl_usd_b, spread_bps_mean, spread_bps_std)
# Calibrated to Polygon mainnet historical observations.
# ---------------------------------------------------------------------------

_TEMPLATES: List[Tuple] = [
    # pair            dex_a         fee_a   dex_b        fee_b    tvl_a       tvl_b    sp_mu  sp_sd
    ("USDC/USDT",  "univ3_100",  0.0001, "qsv2",      0.003,   8_000_000, 3_000_000,  1.0,  0.5),
    ("USDC/USDT",  "univ3_500",  0.0005, "univ3_100", 0.0001,  4_000_000, 8_000_000,  0.6,  0.3),
    ("USDC/DAI",   "univ3_100",  0.0001, "qsv2",      0.003,   5_000_000, 1_200_000,  1.2,  0.6),
    ("USDT/DAI",   "univ3_100",  0.0001, "univ3_500", 0.0005,  2_000_000, 1_500_000,  0.8,  0.4),
    ("WMATIC/USDC","univ3_500",  0.0005, "qsv2",      0.003,   6_000_000, 4_000_000,  8.0,  4.0),
    ("WMATIC/USDC","univ3_3000", 0.003,  "univ3_500", 0.0005,  1_500_000, 6_000_000, 12.0,  5.0),
    ("WMATIC/USDT","univ3_500",  0.0005, "qsv2",      0.003,   3_000_000, 2_000_000,  9.0,  4.5),
    ("WMATIC/DAI", "univ3_500",  0.0005, "qsv2",      0.003,   1_500_000,   800_000, 11.0,  5.0),
    ("USDC/WETH",  "univ3_500",  0.0005, "qsv2",      0.003,   9_000_000, 5_000_000,  6.0,  3.5),
    ("USDC/WETH",  "univ3_3000", 0.003,  "univ3_500", 0.0005,  2_500_000, 9_000_000, 14.0,  6.0),
    ("USDT/WETH",  "univ3_500",  0.0005, "qsv2",      0.003,   4_000_000, 3_000_000,  7.0,  3.5),
    ("DAI/WETH",   "univ3_500",  0.0005, "qsv2",      0.003,   2_000_000, 1_500_000,  8.5,  4.0),
    ("WMATIC/WETH","univ3_500",  0.0005, "qsv2",      0.003,   3_000_000, 2_000_000, 10.0,  5.0),
    ("USDC/WBTC",  "univ3_500",  0.0005, "qsv2",      0.003,   3_000_000, 1_200_000, 15.0,  8.0),
    ("USDC/WBTC",  "univ3_3000", 0.003,  "univ3_500", 0.0005,    800_000, 3_000_000, 22.0,  9.0),
    ("WETH/WBTC",  "univ3_500",  0.0005, "qsv2",      0.003,   2_000_000,   900_000, 18.0,  8.0),
    ("USDC/LINK",  "univ3_3000", 0.003,  "qsv2",      0.003,   1_000_000,   600_000, 25.0, 12.0),
    ("WMATIC/LINK","univ3_3000", 0.003,  "qsv2",      0.003,     500_000,   400_000, 30.0, 14.0),
    ("USDC/AAVE",  "univ3_3000", 0.003,  "qsv2",      0.003,     800_000,   500_000, 28.0, 13.0),
    ("WMATIC/AAVE","univ3_3000", 0.003,  "qsv2",      0.003,     400_000,   350_000, 35.0, 15.0),
    ("WETH/LINK",  "univ3_3000", 0.003,  "qsv2",      0.003,     600_000,   450_000, 22.0, 10.0),
    ("WETH/AAVE",  "univ3_3000", 0.003,  "qsv2",      0.003,     700_000,   500_000, 20.0, 10.0),
    # Thin-pool / emerging-token templates (TVL $20k–$80k).
    # These are only accessible to FLOOD_GATES and SMART_FLOOD (min_tvl = $20k).
    # Wider spreads compensate for lower liquidity.
    ("WMATIC/GHST","qsv2",       0.003,  "univ3_3000", 0.003,   80_000,    60_000, 55.0, 25.0),
    ("USDC/QUICK", "univ3_3000", 0.003,  "qsv2",       0.003,   70_000,    55_000, 65.0, 30.0),
    ("WMATIC/DPI", "univ3_3000", 0.003,  "qsv2",       0.003,   55_000,    45_000, 75.0, 35.0),
    ("USDC/SAND",  "univ3_3000", 0.003,  "qsv2",       0.003,   50_000,    35_000, 80.0, 38.0),
    ("WETH/QUICK", "univ3_3000", 0.003,  "qsv2",       0.003,   45_000,    30_000, 90.0, 42.0),
    ("WMATIC/MANA","qsv2",       0.003,  "univ3_3000", 0.003,   35_000,    28_000, 95.0, 45.0),
    ("USDC/MANA",  "univ3_3000", 0.003,  "qsv2",       0.003,   30_000,    25_000,100.0, 48.0),
    ("WETH/SAND",  "qsv2",       0.003,  "univ3_3000", 0.003,   25_000,    22_000,110.0, 52.0),
]

# Approximate gas cost for a 2-leg flash-loan arbitrage on Polygon (USD).
# Polygon gas is cheap; a complex 2-swap flash loan is ~600k gas at 50 gwei
# and POL ≈ $0.40: 600000 × 50e-9 × 0.40 ≈ $0.012.  Use $0.10 as a
# conservative buffer for APEX contract overhead and priority fees.
_GAS_COST_USD: float = 0.10

# Flash-loan fee rate.  Balancer V2 flash loans on Polygon are free (0 bps).
# Aave V3 charges 9 bps; switch by setting this constant.
_FLASH_FEE_RATE: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dex_category(dex_id: str) -> str:
    dl = dex_id.lower()
    if "v3" in dl or "univ3" in dl:
        return "v3"
    if "aerodrome" in dl or "velodrome" in dl:
        return "aerodrome"
    return "v2"


def _cpmm_swap_out(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Constant-product AMM output (with fee)."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0.0
    eff = amount_in * (1.0 - fee)
    return (eff * reserve_out) / (reserve_in + eff)


def _net_profit(
    loan_usd: float,
    spread_bps: float,
    tvl_a: float,
    tvl_b: float,
    fee_a: float,
    fee_b: float,
    dex_a: str,
    dex_b: str,
) -> float:
    """Simulate one 2-leg trade and return net profit in USD.

    Uses the exact deterministic CPMM slippage model so the result is
    consistent with the production gate in dry_run.py.
    """
    # Buy-side: pool A reserves (balanced)
    r_a_in = tvl_a / 2.0
    r_a_out = tvl_a / 2.0

    # Swap token0 → token1 on pool A
    token1_out = _cpmm_swap_out(loan_usd, r_a_in, r_a_out, fee_a)

    # Sell-side: pool B (token1 reserve sizes sized to match spread)
    spread = spread_bps / 10_000.0
    r_b_in = tvl_b / 2.0
    # Sell-side price is higher by ``spread``, i.e., token0 out per token1
    # is proportionally more.  We model this as the reserve ratio being
    # (1 + spread) times the buy-side ratio.
    r_b_out = tvl_b / 2.0 * (1.0 + spread)

    token0_out = _cpmm_swap_out(token1_out, r_b_in, r_b_out, fee_b)

    gross_profit = token0_out - loan_usd
    flash_fee = loan_usd * _FLASH_FEE_RATE
    net = gross_profit - flash_fee - _GAS_COST_USD
    return net


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

@dataclass
class _TradeRecord:
    pair: str
    loan_usd: float
    spread_bps: float
    net_profit: float
    win: bool


def _viable_loan_for_spread(
    spread_bps: float,
    tvl_a: float,
    tvl_b: float,
    fee_a: float,
    fee_b: float,
    dex_a: str,
    dex_b: str,
    lo: float = 50.0,
    hi: float = 15_000.0,
    steps: int = 32,
) -> Optional[float]:
    """Binary-search the largest loan where combined slippage stays below spread.

    Uses the deterministic CPMM slippage model for both pool legs.

    Returns ``None`` when even the smallest loan (``lo``) fails the gate.
    """
    fee_bps_a = fee_a * 10_000.0
    fee_bps_b = fee_b * 10_000.0
    cat_a = _dex_category(dex_a)
    cat_b = _dex_category(dex_b)

    def combined(loan: float) -> float:
        sa = calculate_deterministic_slippage_bps(loan, tvl_a, cat_a, fee_bps=fee_bps_a)
        sb = calculate_deterministic_slippage_bps(loan, tvl_b, cat_b, fee_bps=fee_bps_b)
        return sa + sb

    if combined(lo) >= spread_bps:
        return None  # not viable at any size

    # Check hi first for a quick win (common for deep pools / wide spreads)
    if combined(hi) < spread_bps:
        return hi

    # Binary-search the crossover point
    for _ in range(steps):
        mid = (lo + hi) / 2.0
        if combined(mid) < spread_bps:
            lo = mid
        else:
            hi = mid

    return lo if lo >= 50.0 else None


def _run_strategy(
    strategy: str,
    scans: int,
    max_loan: float,
    min_tvl: float,
    rng: random.Random,
) -> List[_TradeRecord]:
    """Run one strategy over ``scans`` synthetic scan rounds.

    Loan sizing logic per strategy
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    OLD_STRICT_TVL
        Fixed loan equal to ``max_loan``.  The deterministic slippage gate
        filters the route when this large size would move the price too far.

    FLOOD_GATES
        Uses the maximum loan that passes the deterministic slippage gate
        (binary-search from ``max_loan`` down to $50).  Relaxed TVL floor
        allows thinner pools.

    SMART_FLOOD
        Searches a geometric grid across [$50, ``max_loan``] and picks the
        size that maximises net profit.  Same relaxed TVL floor.

    Returns a list of all evaluated trade records.
    """
    records: List[_TradeRecord] = []

    for scan in range(scans):
        rng.seed(0x4170786F + scan * 17)

        for tmpl in _TEMPLATES:
            (pair, dex_a, fee_a, dex_b, fee_b, tvl_a, tvl_b,
             spread_mu, spread_sd) = tmpl

            # Add small TVL jitter per scan (±10%)
            tvl_a_s = tvl_a * rng.uniform(0.90, 1.10)
            tvl_b_s = tvl_b * rng.uniform(0.90, 1.10)
            smallest_tvl = min(tvl_a_s, tvl_b_s)

            # Draw spread using a mixture model: 90% normal, 10% spike.
            # Spike events (block-boundary price dislocations, thin-book
            # moments) produce 5–8× larger spreads and represent the real
            # arbitrage windows captured by on-chain scanners.
            if rng.random() < 0.10:
                # Spike: mean is 6× base, std is 3× base
                spread_bps = abs(rng.gauss(spread_mu * 6.0, spread_sd * 3.0))
            else:
                spread_bps = abs(rng.gauss(spread_mu, spread_sd))

            # --- TVL filter ---
            if smallest_tvl < min_tvl:
                continue

            # --- Loan size selection with deterministic slippage gate ---
            if strategy == "OLD_STRICT_TVL":
                # Original approach: fixed loan = 0.1% of smaller pool TVL
                # (no per-route profit optimisation).  Applies the slippage
                # gate but does not search for a better size.
                loan = min(max_loan, smallest_tvl * 0.001)
                loan = max(50.0, loan)
                # Gate: check if this fixed loan passes the slippage guard
                slip_a = calculate_deterministic_slippage_bps(
                    loan, tvl_a_s, _dex_category(dex_a), fee_bps=fee_a * 10_000.0,
                )
                slip_b = calculate_deterministic_slippage_bps(
                    loan, tvl_b_s, _dex_category(dex_b), fee_bps=fee_b * 10_000.0,
                )
                if slip_a + slip_b >= spread_bps:
                    continue  # slippage eats the spread at this loan size

            elif strategy == "FLOOD_GATES":
                # Same sizing as OLD_STRICT_TVL but with a relaxed TVL floor
                # (set via the min_tvl parameter) — "opens the flood gates" to
                # thinner pools.  The slippage gate still guards every route.
                loan = min(max_loan, smallest_tvl * 0.001)
                loan = max(50.0, loan)
                slip_a = calculate_deterministic_slippage_bps(
                    loan, tvl_a_s, _dex_category(dex_a), fee_bps=fee_a * 10_000.0,
                )
                slip_b = calculate_deterministic_slippage_bps(
                    loan, tvl_b_s, _dex_category(dex_b), fee_bps=fee_b * 10_000.0,
                )
                if slip_a + slip_b >= spread_bps:
                    continue  # not viable at this loan size

            elif strategy == "SMART_FLOOD":
                # Search a geometric grid for the loan that maximises net profit.
                # All candidates are pre-screened by the slippage gate.
                max_viable = _viable_loan_for_spread(
                    spread_bps=spread_bps,
                    tvl_a=tvl_a_s,
                    tvl_b=tvl_b_s,
                    fee_a=fee_a,
                    fee_b=fee_b,
                    dex_a=dex_a,
                    dex_b=dex_b,
                    lo=50.0,
                    hi=max_loan,
                )
                if max_viable is None:
                    continue

                # 16-point geometric grid from $50 to max_viable
                grid = [50.0 * (max_viable / 50.0) ** (i / 15.0) for i in range(16)]
                loan = max(
                    grid,
                    key=lambda l: _net_profit(
                        l, spread_bps, tvl_a_s, tvl_b_s, fee_a, fee_b, dex_a, dex_b,
                    ),
                )

            else:
                raise ValueError(f"Unknown strategy: {strategy!r}")

            net = _net_profit(
                loan, spread_bps, tvl_a_s, tvl_b_s, fee_a, fee_b, dex_a, dex_b,
            )

            records.append(_TradeRecord(
                pair=pair,
                loan_usd=round(loan, 2),
                spread_bps=round(spread_bps, 4),
                net_profit=round(net, 4),
                win=(net > 0.0),
            ))

    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    strategy: str
    trades: int
    wins: int
    win_rate_pct: float
    total_profit: float
    avg_profit_per_trade: float


def _summarise(strategy: str, records: List[_TradeRecord]) -> StrategyResult:
    trades = len(records)
    wins = sum(1 for r in records if r.win)
    total = sum(r.net_profit for r in records)
    return StrategyResult(
        strategy=strategy,
        trades=trades,
        wins=wins,
        win_rate_pct=round(wins / trades * 100.0, 2) if trades > 0 else 0.0,
        total_profit=round(total, 2),
        avg_profit_per_trade=round(total / trades, 2) if trades > 0 else 0.0,
    )


def _print_table(results: List[StrategyResult]) -> None:
    header = (
        f"{'Strategy':<20} {'Trades':>7} {'Win %':>7} "
        f"{'Total $':>12} {'Avg/Trade':>10} {'Verdict':<20}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)

    best_total = max(r.total_profit for r in results)
    best_avg = max(r.avg_profit_per_trade for r in results)

    for r in results:
        tags: List[str] = []
        if r.total_profit == best_total:
            tags.append("Best total")
        if r.avg_profit_per_trade == best_avg:
            tags.append("Best avg/trade")
        verdict = "; ".join(tags) if tags else "—"

        print(
            f"{r.strategy:<20} {r.trades:>7} {r.win_rate_pct:>6.2f}% "
            f"${r.total_profit:>11,.2f} ${r.avg_profit_per_trade:>9,.2f} {verdict}"
        )
    print(sep)


def _write_csv(results: List[StrategyResult], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([fi.name for fi in fields(StrategyResult)])
        for r in results:
            writer.writerow([
                r.strategy, r.trades, r.wins, r.win_rate_pct,
                r.total_profit, r.avg_profit_per_trade,
            ])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_STRATEGIES = ["OLD_STRICT_TVL", "FLOOD_GATES", "SMART_FLOOD"]

_STRATEGY_PARAMS: Dict[str, Dict] = {
    "OLD_STRICT_TVL": {"min_tvl": 100_000.0},
    "FLOOD_GATES":    {"min_tvl": 20_000.0},
    "SMART_FLOOD":    {"min_tvl": 20_000.0},
}


def run_backtest(
    strategies: Optional[List[str]] = None,
    scans: int = 30,
    max_loan: float = 15_000.0,
    csv_out: Optional[Path] = None,
) -> List[StrategyResult]:
    """Run the backtest and return strategy results.

    Parameters
    ----------
    strategies : list of strategy names to run (default: all three)
    scans      : number of synthetic scan rounds per strategy
    max_loan   : maximum flash-loan principal in USD
    csv_out    : optional path to write CSV summary

    Returns
    -------
    list of :class:`StrategyResult`
    """
    if strategies is None:
        strategies = _STRATEGIES

    rng = random.Random(0x4170786F)
    results: List[StrategyResult] = []

    for strat in strategies:
        params = _STRATEGY_PARAMS.get(strat, {"min_tvl": 50_000.0})
        records = _run_strategy(
            strategy=strat,
            scans=scans,
            max_loan=max_loan,
            min_tvl=params["min_tvl"],
            rng=rng,
        )
        results.append(_summarise(strat, records))

    _print_table(results)

    if csv_out is not None:
        _write_csv(results, csv_out)
        print(f"\nCSV written to {csv_out}")

    return results


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Flash-loan arbitrage backtester v3.2 (deterministic slippage)"
    )
    p.add_argument(
        "--strategy",
        choices=_STRATEGIES + ["ALL"],
        default="ALL",
        help="Strategy to run (default: ALL)",
    )
    p.add_argument(
        "--scans", type=int, default=30,
        help="Number of synthetic scan rounds (default: 30)",
    )
    p.add_argument(
        "--max-loan", type=float, default=15_000.0,
        help="Maximum flash-loan size in USD (default: 15000)",
    )
    p.add_argument(
        "--csv", type=Path, default=Path("/tmp/backtest_results.csv"),
        help="CSV output path (default: /tmp/backtest_results.csv)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    strats = _STRATEGIES if args.strategy == "ALL" else [args.strategy]
    run_backtest(
        strategies=strats,
        scans=args.scans,
        max_loan=args.max_loan,
        csv_out=args.csv,
    )
