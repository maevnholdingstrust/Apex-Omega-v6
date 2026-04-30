"""Route graph: N-hop cycle enumeration and simulation for multi-leg DEX arbitrage.

This module builds an in-memory adjacency graph over token → pool edges and
exposes deterministic helpers for finding and scoring multi-hop arbitrage
cycles up to ``max_hops`` legs.

Public API
----------
CycleRecord
    Fully-scored result for a single N-hop cycle.
RouteGraph
    Token adjacency graph built from a ``pool_map`` dict.
enumerate_cycles
    Module-level helper: enumerate all simple cycles of a given hop range.
simulate_n_hop_cycle
    Simulate one ordered token sequence at a fixed input amount.
scan_multi_hop_cycles
    Grid-search all cycles in a pool map and return owner-positive records.

Design notes
------------
* All swap arithmetic uses constant-product (CPMM) math for ``kind="cpmm"``
  pools and the Curve StableSwap Newton solver for ``kind="curve_ss"`` pools.
  The math mirrors :mod:`dry_run` exactly so both produce identical results
  for the same inputs.
* ``RouteGraph`` is pool-type agnostic: it accepts any object that exposes
  the ``_POOL_ATTRS`` protocol (see below).  ``_PoolSnapshot`` from
  ``dry_run`` and any future pool dataclass satisfy this protocol.
* ``scan_multi_hop_cycles`` is pure-Python and deterministic — no RPC, no
  random state, no side effects.  It is safe to call from unit tests.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pool attribute protocol
# Any object used with RouteGraph must have these attributes.
# ---------------------------------------------------------------------------

_POOL_ATTRS = (
    "pool_address",
    "dex",
    "fee",
    "sym0",
    "sym1",
    "reserve0",
    "reserve1",
    "price",
    "kind",   # "cpmm" | "curve_ss"
    "amp",    # float (Curve A coefficient; 0.0 for CPMM pools)
)


# ---------------------------------------------------------------------------
# CPMM and StableSwap swap math (mirrors dry_run helpers)
# ---------------------------------------------------------------------------

def _cpmm_swap_out(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Constant-product AMM: tokens received for ``amount_in``."""
    if amount_in <= 0.0 or reserve_in <= 0.0 or reserve_out <= 0.0:
        return 0.0
    eff_in = amount_in * (1.0 - fee)
    return (eff_in * reserve_out) / (reserve_in + eff_in)


def _curve_get_D(balances: List[float], A: float) -> float:
    """Curve StableSwap invariant D (Newton solver)."""
    n = len(balances)
    S = sum(balances)
    if S == 0.0:
        return 0.0
    Ann = A * (n ** n)
    D = S
    for _ in range(255):
        D_P = D
        for x in balances:
            D_P = D_P * D / (x * n)
        D_prev = D
        D = (Ann * S + D_P * n) * D / ((Ann - 1) * D + (n + 1) * D_P)
        if abs(D - D_prev) <= 1e-9:
            break
    return D


def _curve_get_y(i: int, j: int, x_new: float,
                 balances: List[float], A: float, D: float) -> float:
    """Solve Curve invariant for new balance of coin j given new balance of coin i."""
    n = len(balances)
    Ann = A * (n ** n)
    c = D
    S_ = 0.0
    for k in range(n):
        if k == j:
            continue
        _x = x_new if k == i else balances[k]
        S_ += _x
        c = c * D / (_x * n)
    c = c * D / (Ann * n)
    b = S_ + D / Ann
    y = D
    for _ in range(255):
        y_prev = y
        y = (y * y + c) / (2.0 * y + b - D)
        if abs(y - y_prev) <= 1e-9:
            break
    return y


def _curve_get_dy(i: int, j: int, dx: float,
                  balances: List[float], A: float, fee: float) -> float:
    """Curve StableSwap: tokens received for ``dx`` of coin i."""
    if dx <= 0.0:
        return 0.0
    D = _curve_get_D(balances, A)
    if D <= 0.0:
        return 0.0
    x_new = balances[i] + dx
    y_new = _curve_get_y(i, j, x_new, balances, A, D)
    dy = balances[j] - y_new
    return max(0.0, dy * (1.0 - fee))


def _pool_swap_out(amount_in: float, pool: Any, swap_0_to_1: bool) -> float:
    """Dispatch swap math by pool kind."""
    kind = getattr(pool, "kind", "cpmm")
    if kind == "curve_ss":
        i, j = (0, 1) if swap_0_to_1 else (1, 0)
        balances = [pool.reserve0, pool.reserve1]
        return _curve_get_dy(i, j, amount_in, balances, pool.amp, pool.fee)
    # CPMM default
    r_in, r_out = (
        (pool.reserve0, pool.reserve1) if swap_0_to_1 else (pool.reserve1, pool.reserve0)
    )
    return _cpmm_swap_out(amount_in, r_in, r_out, pool.fee)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CycleRecord:
    """Fully-scored result for a single N-hop arbitrage cycle.

    All monetary fields are in USD unless the field name ends in a token
    unit (e.g. ``amount_in`` is in start-token native units).

    ``profitable`` is True only when ``net_profit_usd >= min_net_profit_usd``
    as evaluated by :func:`scan_multi_hop_cycles`.  Callers should NOT
    substitute their own threshold after the fact.
    """
    # Route identity
    tokens: List[str]       # token sequence: [A, B, …, A] (start == end)
    pools: List[str]        # pool addresses in hop order
    dexes: List[str]        # dex labels in hop order
    hop_count: int          # number of swaps (= len(tokens) − 1)

    # Sizing
    amount_in: float        # input principal (start-token native units)
    trade_size_usd: float   # amount_in × token_price_usd

    # Profit decomposition
    amount_out: float       # output after all swaps (start-token native units)
    gross_profit: float     # amount_out − amount_in (start-token units)
    gross_profit_usd: float # gross_profit × token_price_usd

    # Costs
    flash_fee_usd: float    # flash-loan fee on the principal
    gas_cost_usd: float     # EIP-1559 gas cost at optimal tip

    # Net metrics
    net_profit_usd: float   # gross_profit_usd − flash_fee_usd − gas_cost_usd
    p_fill: float           # P(inclusion in next block) at optimal tip
    e_profit: float         # net_profit_usd × p_fill  (0 when net_profit_usd ≤ 0)
    profitable: bool        # net_profit_usd ≥ caller's min_net_profit_usd threshold


# ---------------------------------------------------------------------------
# RouteGraph
# ---------------------------------------------------------------------------

class RouteGraph:
    """Token adjacency graph over DEX pools.

    Nodes are token symbols; directed edges are pools (each pool contributes
    two directed edges: token0 → token1 and token1 → token0).

    Parameters
    ----------
    pool_map
        ``{pair_key: [pool, …]}`` dict, where ``pair_key = "sym0/sym1"`` and
        each pool is any object satisfying the ``_POOL_ATTRS`` protocol.
    """

    def __init__(self, pool_map: Dict[str, List[Any]]) -> None:
        # _adj[from_sym][to_sym] = [pool, …]
        self._adj: Dict[str, Dict[str, List[Any]]] = {}
        for pair_key, pools in pool_map.items():
            sym0, sym1 = pair_key.split("/")
            self._adj.setdefault(sym0, {}).setdefault(sym1, []).extend(pools)
            self._adj.setdefault(sym1, {}).setdefault(sym0, []).extend(pools)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def tokens(self) -> List[str]:
        """Sorted list of all token symbols in the graph."""
        return sorted(self._adj.keys())

    def neighbors(self, token: str) -> List[str]:
        """All tokens reachable from ``token`` in one hop."""
        return list(self._adj.get(token, {}).keys())

    def pools_for_edge(self, from_sym: str, to_sym: str) -> List[Any]:
        """All pools that can execute the ``from_sym → to_sym`` swap."""
        return self._adj.get(from_sym, {}).get(to_sym, [])

    def best_pool_for_edge(
        self, from_sym: str, to_sym: str
    ) -> Optional[Tuple[Any, bool]]:
        """Return ``(pool, swap_0_to_1)`` for the price-optimal eligible pool.

        Selection criterion: the pool that maximises the output-per-unit-input
        for the ``from_sym → to_sym`` direction.

        * When ``from_sym == pool.sym0`` (swap token0 → token1): prefer the
          pool with the **highest** ``reserve1 / reserve0`` ratio — more
          token1 received per token0 sent.
        * When ``from_sym == pool.sym1`` (swap token1 → token0): prefer the
          pool with the **highest** ``reserve0 / reserve1`` ratio — more
          token0 received per token1 sent.

        Pools with zero reserves on either side are excluded.

        Returns ``None`` when no eligible pool exists.
        """
        pools = [
            p for p in self.pools_for_edge(from_sym, to_sym)
            if p.reserve0 > 0.0 and p.reserve1 > 0.0
        ]
        if not pools:
            return None

        def _price_key(p: Any) -> float:
            if p.sym0 == from_sym:
                # Swapping token0 → token1: higher reserve1/reserve0 = better rate
                return p.reserve1 / p.reserve0
            else:
                # Swapping token1 → token0: higher reserve0/reserve1 = better rate
                return p.reserve0 / p.reserve1

        best = max(pools, key=_price_key)
        swap_0_to_1 = (best.sym0 == from_sym)
        return best, swap_0_to_1

    def enumerate_cycles(
        self,
        start_token: str,
        min_hops: int = 2,
        max_hops: int = 4,
    ) -> List[List[str]]:
        """Enumerate all simple cycles beginning and ending at ``start_token``.

        Returns a list of token sequences ``[start, t1, …, start]`` (the
        start token appears at both ends).  The intermediate tokens are all
        distinct; no token appears twice in the interior.

        Parameters
        ----------
        start_token
            Starting (and ending) token for all cycles.
        min_hops
            Minimum number of swaps (≥ 2 to be a real cycle).
        max_hops
            Maximum number of swaps (bounded to prevent combinatorial explosion).

        Notes
        -----
        The search is a depth-limited DFS.  For ``max_hops=4`` and a graph
        with ~30 tokens the enumeration completes in microseconds.
        """
        if start_token not in self._adj:
            return []
        min_hops = max(min_hops, 2)
        max_hops = max(max_hops, min_hops)

        results: List[List[str]] = []
        # DFS state: (current_token, path_so_far_including_start)
        stack: List[Tuple[str, List[str]]] = [(start_token, [start_token])]
        while stack:
            current, path = stack.pop()
            depth = len(path) - 1  # hops so far
            if depth >= min_hops and current == start_token and len(path) > 1:
                results.append(path[:])
                # Don't extend further — we've closed the cycle
                if depth >= max_hops:
                    continue
            if depth >= max_hops:
                continue
            for nxt in self.neighbors(current):
                # Allow returning to start only when we've met min_hops
                if nxt == start_token:
                    if depth + 1 >= min_hops:
                        results.append(path + [start_token])
                    continue
                # No revisiting interior nodes
                if nxt in path:
                    continue
                stack.append((nxt, path + [nxt]))
        return results


# ---------------------------------------------------------------------------
# Cycle simulation
# ---------------------------------------------------------------------------

def simulate_n_hop_cycle(
    graph: RouteGraph,
    token_sequence: List[str],
    amount_in: float,
) -> Tuple[float, List[Tuple[Any, bool]]]:
    """Simulate one N-hop cycle and return ``(amount_out, leg_info)``.

    Parameters
    ----------
    graph
        Pre-built :class:`RouteGraph`.
    token_sequence
        Ordered token list, e.g. ``["WMATIC", "USDC", "WETH", "WMATIC"]``.
        The first and last element must be the same token.
    amount_in
        Input amount in start-token native units.

    Returns
    -------
    (amount_out, leg_info)
        ``amount_out`` is the final output in start-token units (``amount_in``
        when no pools are found or any swap returns 0).
        ``leg_info`` is a list of ``(pool, swap_0_to_1)`` tuples for each hop.
    """
    if len(token_sequence) < 3 or token_sequence[0] != token_sequence[-1]:
        return 0.0, []

    leg_info: List[Tuple[Any, bool]] = []
    current_amount = amount_in
    for i in range(len(token_sequence) - 1):
        from_sym = token_sequence[i]
        to_sym = token_sequence[i + 1]
        edge = graph.best_pool_for_edge(from_sym, to_sym)
        if edge is None:
            return 0.0, []
        pool, swap_0_to_1 = edge
        leg_info.append((pool, swap_0_to_1))
        current_amount = _pool_swap_out(current_amount, pool, swap_0_to_1)
        if current_amount <= 0.0:
            return 0.0, leg_info

    return current_amount, leg_info


# ---------------------------------------------------------------------------
# Multi-hop scanner
# ---------------------------------------------------------------------------

def scan_multi_hop_cycles(
    pool_map: Dict[str, List[Any]],
    token_prices: Dict[str, float],
    tip_optimizer: Any,  # TipOptimizer — typed as Any to avoid circular import
    min_hops: int = 2,
    max_hops: int = 4,
    max_trade_size_usd: float = 10_000.0,
    flash_loan_fee_rate: float = 0.0,
    min_net_profit_usd: float = 1.0,
    gas_units_multiplier: float = 1.0,
    grid_points: int = 16,
) -> List[CycleRecord]:
    """Grid-search all N-hop cycles in ``pool_map`` and return owner-positive
    :class:`CycleRecord` instances.

    The scan is fully deterministic: no RPC calls, no random state.

    Parameters
    ----------
    pool_map
        ``{pair_key: [pool, …]}`` dict (same format as :func:`_discover_pools`
        in ``dry_run``).
    token_prices
        USD price per token symbol (start-token pricing).
    tip_optimizer
        A :class:`~apex_omega_core.core.mev_gas_oracle.TipOptimizer` instance
        used to estimate gas cost and P(fill) at the optimal tip.
    min_hops
        Minimum number of swaps per cycle (2 = two-leg, etc.).
    max_hops
        Maximum number of swaps per cycle.
    max_trade_size_usd
        Upper cap on trade size (in USD).
    flash_loan_fee_rate
        Flash-loan fee as a decimal (e.g. 0.0009 for Aave V3 9 bps).
    min_net_profit_usd
        Minimum net profit (after all fees) to emit a record.
    gas_units_multiplier
        Multiplier applied to the TipOptimizer's base gas cost to account for
        N-hop complexity (e.g. 1.5 for 3-hop routes).
    grid_points
        Number of points in the geometric input-size grid.

    Returns
    -------
    List[CycleRecord]
        All cycles with ``net_profit_usd >= min_net_profit_usd``, sorted by
        ``e_profit`` descending.
    """
    graph = RouteGraph(pool_map)
    out: List[CycleRecord] = []

    # Build a geometric size grid: [50, …, max_trade_size_usd] with grid_points steps
    n_pts = max(grid_points, 2)
    size_grid_usd = [
        50.0 * (max_trade_size_usd / 50.0) ** (i / (n_pts - 1))
        for i in range(n_pts)
    ]

    for start_token in graph.tokens:
        price_usd = token_prices.get(start_token, 0.0)
        if price_usd <= 0.0:
            continue

        cycles = graph.enumerate_cycles(start_token, min_hops=min_hops, max_hops=max_hops)
        if not cycles:
            continue

        for cycle_tokens in cycles:
            hop_count = len(cycle_tokens) - 1

            best_net = float("-inf")
            best_rec: Optional[CycleRecord] = None

            for size_usd in size_grid_usd:
                amount_in = size_usd / price_usd
                amount_out, leg_info = simulate_n_hop_cycle(graph, cycle_tokens, amount_in)
                if amount_out <= 0.0 or not leg_info:
                    continue

                gross = (amount_out - amount_in) * price_usd
                flash_fee = size_usd * flash_loan_fee_rate

                # Gas cost scales with hop count.  Three-hop ≈ 1.5×, four-hop ≈ 2×.
                hop_gas_mult = gas_units_multiplier * (1.0 + (hop_count - 2) * 0.5)
                hop_gas_mult = max(hop_gas_mult, 1.0)
                eip1559 = tip_optimizer.build_eip1559_params(
                    max(gross - flash_fee, 0.01)
                )
                gas_cost = eip1559["gas_cost_usd"] * hop_gas_mult
                net = gross - flash_fee - gas_cost

                if net > best_net:
                    best_net = net
                    p_fill = eip1559["p_fill"]
                    pools_used = [p.pool_address for p, _ in leg_info]
                    dexes_used = [p.dex for p, _ in leg_info]
                    best_rec = CycleRecord(
                        tokens=cycle_tokens[:],
                        pools=pools_used,
                        dexes=dexes_used,
                        hop_count=hop_count,
                        amount_in=amount_in,
                        trade_size_usd=size_usd,
                        amount_out=amount_out,
                        gross_profit=amount_out - amount_in,
                        gross_profit_usd=gross,
                        flash_fee_usd=flash_fee,
                        gas_cost_usd=gas_cost,
                        net_profit_usd=net,
                        p_fill=p_fill,
                        e_profit=net * p_fill if net > 0.0 else 0.0,
                        profitable=(net >= min_net_profit_usd),
                    )

            if best_rec is not None and best_rec.net_profit_usd >= min_net_profit_usd:
                out.append(best_rec)

    # Stable sort: highest expected profit first.
    out.sort(key=lambda r: r.e_profit, reverse=True)
    return out
