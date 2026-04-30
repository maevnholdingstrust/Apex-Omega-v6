"""Multi-hop multi-pair graph router for arbitrage cycle discovery.

The pool universe is modelled as a directed multigraph:

    nodes  = token symbols (strings)
    edges  = pool swap directions
             Each undirected pool (sym0 ↔ sym1) becomes two directed edges:
               sym0 → sym1  (swap_0_to_1=True)
               sym1 → sym0  (swap_0_to_1=False)

``RouteGraph.enumerate_cycles`` performs a DFS to find all simple-token
cycles of length [min_hops, max_hops] that start and end at the same
origin token.  For each cycle, ``best_pool_for_leg`` selects the pool
with the deepest combined reserves, and ``simulate_n_hop_cycle`` chains
the CPMM (or Curve StableSwap) swap math across every hop.

Two-leg same-pair (cross-DEX) arb is handled by ``_compute_opportunity``
in dry_run.py using the closed-form Angeris-Chitra optimal-input formula.
This module handles 3-hop and beyond.

Typical usage in dry_run.py:

    from apex_omega_core.core.route_graph import RouteGraph

    graph = RouteGraph.build_from_pool_map(pool_map)
    results = scan_multi_hop_cycles(
        graph, pool_map, token_prices, tip_optimizer,
        max_hops=4, flash_loan_fee_rate=0.0,
        min_net_profit_usd=1.0,
    )

Each result is a ``CycleRecord`` whose fields mirror ``OpportunityRecord``
so that downstream consumers can treat them identically.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pool-protocol typing
# ---------------------------------------------------------------------------

# ``PoolLike`` is a structural interface: any object that has these attributes
# is accepted by the graph router.  ``dry_run._PoolSnapshot`` matches exactly.

class PoolLike:
    """Documentation-only stub showing the expected pool interface.

    The router uses duck-typing; you do not need to inherit from this class.
    Any object with the following attributes is accepted:

    * ``pool_address: str``
    * ``dex: str``
    * ``fee: float``        — decimal, e.g. 0.003
    * ``sym0: str``         — token0 symbol (canonical lower-address ordering)
    * ``sym1: str``         — token1 symbol
    * ``reserve0: float``   — token0 normalised reserves
    * ``reserve1: float``   — token1 normalised reserves
    * ``price: float``      — token1 / token0 (both normalised)
    * ``kind: str``         — "cpmm" | "curve_ss"
    * ``amp: float``        — Curve amplification (ignored unless kind=="curve_ss")
    """


# ---------------------------------------------------------------------------
# Graph edge
# ---------------------------------------------------------------------------

@dataclass
class GraphEdge:
    """One directed hop in the token multigraph.

    ``swap_0_to_1=True``  →  pool.sym0 → pool.sym1  (sell token0, receive token1)
    ``swap_0_to_1=False`` →  pool.sym1 → pool.sym0  (sell token1, receive token0)
    """
    from_token: str
    to_token: str
    pool: Any          # PoolLike
    swap_0_to_1: bool

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"GraphEdge({self.from_token!r}→{self.to_token!r} "
            f"via {self.pool.dex!r} swap0to1={self.swap_0_to_1})"
        )


# ---------------------------------------------------------------------------
# Swap math helpers
# ---------------------------------------------------------------------------

def _cpmm_swap_out(amount_in: float, r_in: float, r_out: float, fee: float) -> float:
    """Constant-product swap: ``amount_out`` received for ``amount_in``."""
    if amount_in <= 0 or r_in <= 0 or r_out <= 0:
        return 0.0
    eff = amount_in * (1.0 - fee)
    return (eff * r_out) / (r_in + eff)


def _curve_get_D(balances: List[float], A: float) -> float:
    """Curve StableSwap invariant D (Newton's method)."""
    n = len(balances)
    S = sum(balances)
    if S == 0:
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
    """Solve Curve invariant for new balance of coin j."""
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
        y = (y * y + c) / (2 * y + b - D)
        if abs(y - y_prev) <= 1e-9:
            break
    return y


def _curve_get_dy(i: int, j: int, dx: float,
                  balances: List[float], A: float, fee: float) -> float:
    """Curve StableSwap: tokens out for ``dx`` tokens in (2-coin pairwise view)."""
    if dx <= 0:
        return 0.0
    D = _curve_get_D(balances, A)
    if D <= 0:
        return 0.0
    x_new = balances[i] + dx
    y_new = _curve_get_y(i, j, x_new, balances, A, D)
    dy = balances[j] - y_new
    return max(0.0, dy * (1.0 - fee))


def _edge_swap_out(amount_in: float, edge: GraphEdge) -> float:
    """Dispatch swap math for a single directed edge."""
    pool = edge.pool
    if pool.kind == "curve_ss":
        i, j = (0, 1) if edge.swap_0_to_1 else (1, 0)
        return _curve_get_dy(i, j, amount_in,
                             [pool.reserve0, pool.reserve1], pool.amp, pool.fee)
    # CPMM default (UniV3 virtual-reserve approx, V2, Balancer 50/50)
    r_in  = pool.reserve0 if edge.swap_0_to_1 else pool.reserve1
    r_out = pool.reserve1 if edge.swap_0_to_1 else pool.reserve0
    return _cpmm_swap_out(amount_in, r_in, r_out, pool.fee)


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def simulate_n_hop_cycle(legs: Sequence[GraphEdge], amount_in: float) -> float:
    """Chain N swap legs and return the final output amount.

    Returns the token-``from_token`` amount received at the end of the
    cycle (positive means profit over ``amount_in``; negative means loss).

    Returns ``0.0`` immediately if any intermediate output is non-positive
    so that callers can detect degenerate cycles without checking signs.
    """
    out = amount_in
    for edge in legs:
        out = _edge_swap_out(out, edge)
        if out <= 0.0:
            return 0.0
    return out


# ---------------------------------------------------------------------------
# Pool-per-leg selection
# ---------------------------------------------------------------------------

def best_pool_for_leg(
    from_token: str,
    to_token: str,
    pools: List[Any],
) -> Optional[GraphEdge]:
    """Select the deepest-reserve CPMM pool for a directed leg.

    Returns ``None`` when no eligible pool exists (requires ``kind=="cpmm"``
    and positive reserves on both sides).  Curve StableSwap pools are
    excluded from multi-hop cycles because their balanced-reserve property
    makes them unsuitable as pure CPMM legs in a cycle that spans DEXes.

    Deepest is defined as ``max(reserve0 + reserve1)`` after normalising
    direction to the requested from→to ordering.  This heuristic minimises
    price impact on the widest pool while remaining O(N) per leg.
    """
    best: Optional[GraphEdge] = None
    best_depth: float = 0.0

    for pool in pools:
        if pool.reserve0 <= 0 or pool.reserve1 <= 0:
            continue
        if pool.kind != "cpmm":
            continue

        if pool.sym0 == from_token and pool.sym1 == to_token:
            swap_dir = True
        elif pool.sym1 == from_token and pool.sym0 == to_token:
            swap_dir = False
        else:
            continue

        depth = pool.reserve0 + pool.reserve1
        if depth > best_depth:
            best_depth = depth
            best = GraphEdge(from_token, to_token, pool, swap_dir)

    return best


# ---------------------------------------------------------------------------
# RouteGraph
# ---------------------------------------------------------------------------

class RouteGraph:
    """Directed multigraph of token-swap opportunities.

    Nodes  = token symbols (strings).
    Edges  = directed pool swap directions (two per undirected pool).

    Building
    --------
    ::

        graph = RouteGraph.build_from_pool_map(pool_map)

    where ``pool_map`` is ``Dict[str, List[PoolLike]]`` keyed by
    ``"sym0/sym1"`` pair strings.

    Cycle enumeration
    -----------------
    ::

        for cycle_legs in graph.enumerate_cycles("USDC", min_hops=3, max_hops=4):
            profit = simulate_n_hop_cycle(cycle_legs, 1000.0)

    Only simple-token cycles are enumerated (no token visited twice except
    the starting token which appears at both ends).
    """

    def __init__(self) -> None:
        # _adj[token] = list of outgoing GraphEdge objects
        self._adj: Dict[str, List[GraphEdge]] = defaultdict(list)
        # _pairs[frozenset(sym0, sym1)] = list of pools for that pair
        self._pairs: Dict[FrozenSet[str], List[Any]] = defaultdict(list)
        self._tokens: set = set()

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    def add_pool(self, pool: Any) -> None:
        """Register both swap directions for a pool.

        Only CPMM pools with positive reserves on both sides are added.
        Curve StableSwap pools (``kind == "curve_ss"``) are excluded
        because their balanced-balance property means apparent reserve
        asymmetry is not an exploitable price gap.
        """
        if pool.reserve0 <= 0 or pool.reserve1 <= 0:
            return
        if pool.kind != "cpmm":
            return

        sym0, sym1 = pool.sym0, pool.sym1
        self._tokens.add(sym0)
        self._tokens.add(sym1)

        pair_key: FrozenSet[str] = frozenset((sym0, sym1))
        self._pairs[pair_key].append(pool)

        self._adj[sym0].append(GraphEdge(sym0, sym1, pool, True))
        self._adj[sym1].append(GraphEdge(sym1, sym0, pool, False))

    @classmethod
    def build_from_pool_map(
        cls,
        pool_map: Dict[str, List[Any]],
    ) -> "RouteGraph":
        """Construct a ``RouteGraph`` from a ``{pair_key: [pools]}`` mapping.

        ``pair_key`` format is ``"sym0/sym1"`` (canonical lower-address
        ordering).  Pools that fail the ``add_pool`` guards are silently
        skipped.
        """
        graph = cls()
        for pools in pool_map.values():
            for pool in pools:
                graph.add_pool(pool)
        return graph

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tokens(self) -> List[str]:
        """Sorted list of all token symbols in the graph."""
        return sorted(self._tokens)

    def neighbors(self, token: str) -> List[GraphEdge]:
        """All outgoing edges from ``token`` (deduplicated by pool address)."""
        seen: set = set()
        out: List[GraphEdge] = []
        for edge in self._adj.get(token, []):
            if edge.pool.pool_address not in seen:
                seen.add(edge.pool.pool_address)
                out.append(edge)
        return out

    def pools_for_pair(self, token_a: str, token_b: str) -> List[Any]:
        """All pools for the unordered pair (token_a, token_b)."""
        return list(self._pairs.get(frozenset((token_a, token_b)), []))

    # ------------------------------------------------------------------
    # Cycle enumeration
    # ------------------------------------------------------------------

    def enumerate_cycles(
        self,
        start_token: str,
        min_hops: int = 3,
        max_hops: int = 4,
    ) -> Iterator[List[GraphEdge]]:
        """Yield all simple-token cycles that start and end at ``start_token``.

        A *simple-token* cycle is a path where no intermediate token is
        visited more than once; ``start_token`` appears only at the first
        and last position.

        Each yielded cycle is a list of ``GraphEdge`` objects representing
        the directed swap path.  The first edge departs from ``start_token``
        and the last edge arrives back at ``start_token``.

        Parameters
        ----------
        start_token:
            The token that opens and closes the flash-loan cycle.
        min_hops:
            Minimum cycle length (number of swaps).  Default is 3
            because 2-hop same-pair arb is handled separately by
            ``_compute_opportunity``.
        max_hops:
            Maximum cycle length.  Values above 4 are permitted but
            dramatically increase enumeration time on dense graphs.

        Implementation
        --------------
        Iterative DFS.  Each stack frame is a tuple
        ``(current_token, path_so_far, visited_token_set)``.
        """
        if start_token not in self._tokens:
            return

        # DFS stack entries: (current_token, path, visited_tokens_except_start)
        stack: List[Tuple[str, List[GraphEdge], set]] = [
            (start_token, [], set())
        ]

        while stack:
            curr, path, visited = stack.pop()
            hop_count = len(path)

            for edge in self._adj.get(curr, []):
                next_tok = edge.to_token
                new_path = path + [edge]
                new_hop = hop_count + 1

                if next_tok == start_token:
                    if new_hop >= min_hops:
                        yield new_path
                    # Cannot extend a closed cycle, so skip push
                    continue

                if new_hop >= max_hops:
                    continue
                if next_tok in visited:
                    continue

                stack.append((next_tok, new_path, visited | {next_tok}))

    def all_start_cycles(
        self,
        min_hops: int = 3,
        max_hops: int = 4,
    ) -> Iterator[List[GraphEdge]]:
        """Enumerate all simple-token cycles across every token in the graph.

        Avoids duplicate enumeration: once a cycle has been yielded for a
        canonical start token (the lexicographically smallest token in the
        cycle), it is not yielded again from another starting point.
        """
        seen_sigs: set = set()
        for token in self.tokens:
            for cycle in self.enumerate_cycles(token, min_hops=min_hops, max_hops=max_hops):
                # Canonical signature: rotate so the smallest token is first
                tok_seq = [e.from_token for e in cycle]
                min_tok = min(tok_seq)
                idx = tok_seq.index(min_tok)
                sig = tuple(tok_seq[idx:] + tok_seq[:idx])
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                yield cycle


# ---------------------------------------------------------------------------
# CycleRecord — mirrors OpportunityRecord from dry_run.py
# ---------------------------------------------------------------------------

@dataclass
class CycleRecord:
    """Result of a profitable multi-hop cycle scan iteration.

    Fields mirror ``OpportunityRecord`` in dry_run.py so that callers
    can treat them interchangeably in downstream CSV / dashboard code.
    """
    scan_no: int
    timestamp: float
    pair: str              # cycle label, e.g. "USDC->WETH->WMATIC->USDC"
    buy_dex: str           # DEX chain for logging, e.g. "univ3_500->qsv2->univ3_3000"
    sell_dex: str          # "multihop" constant
    buy_pool: str          # first leg pool address
    sell_pool: str         # last leg pool address
    raw_spread_bps: float
    trade_size_usd: float
    gross_profit_usd: float
    slippage_cost_usd: float   # 0.0 — already netted into CPMM math
    gas_cost_usd: float
    expected_net_edge: float
    p_fill: float
    e_profit: float
    profitable: bool
    hop_count: int         # number of swaps in the cycle


# ---------------------------------------------------------------------------
# Multi-hop cycle scanner
# ---------------------------------------------------------------------------

def scan_multi_hop_cycles(
    graph: RouteGraph,
    pool_map: Dict[str, List[Any]],
    token_prices: Dict[str, float],
    tip_optimizer: Any,
    scan_no: int = 0,
    max_trade_size_usd: float = 10_000.0,
    flash_loan_fee_rate: float = 0.0,
    min_net_profit_usd: float = 1.0,
    min_hops: int = 3,
    max_hops: int = 4,
    gas_multiplier: float = 1.5,
    grid_points: int = 24,
    min_size_usd: float = 50.0,
) -> List[CycleRecord]:
    """Search all simple-token cycles in ``graph`` for owner-positive net profit.

    For each cycle ``scan_multi_hop_cycles`` will:

    1. Select the deepest-reserve CPMM pool per leg via
       ``best_pool_for_leg``.
    2. Grid-search over ``grid_points`` logarithmically-spaced trade
       sizes from ``min_size_usd`` to ``max_trade_size_usd`` and pick
       the size that maximises USD profit after DEX fees, slippage, the
       flash-loan fee, and gas.
    3. Emit a :class:`CycleRecord` only when net profit ≥
       ``min_net_profit_usd``.

    Parameters
    ----------
    graph:
        Pre-built ``RouteGraph`` from the live pool universe.
    pool_map:
        Raw ``{pair_key: [pools]}`` mapping (used for best-pool lookup).
    token_prices:
        ``{symbol: usd_price}`` for the tokens in the graph.
    tip_optimizer:
        ``TipOptimizer`` from ``mev_gas_oracle`` (provides EIP-1559 tip
        and ``p_fill`` at each candidate net profit).
    scan_no:
        Current scan iteration number (embedded in records for tracing).
    max_trade_size_usd:
        Maximum flash-loan principal in USD.
    flash_loan_fee_rate:
        Flash-loan fee as a decimal (e.g. 0.0009 for Aave V3 9 bps).
    min_net_profit_usd:
        Owner profit floor; cycles below this threshold are discarded.
    min_hops:
        Minimum cycle length (default 3 — 2-leg same-pair arb is
        handled by ``_compute_opportunity``).
    max_hops:
        Maximum cycle length (default 4).  Values above 4 are accepted
        but enumeration time grows super-linearly on dense graphs.
    gas_multiplier:
        Gas cost scale factor relative to a 2-leg swap.  Default 1.5
        accounts for the overhead of additional hop calls.
    grid_points:
        Number of candidate trade sizes in the logarithmic grid.
    min_size_usd:
        Smallest grid point in USD.

    Returns
    -------
    List[CycleRecord]
        All cycles whose net profit meets ``min_net_profit_usd``, sorted
        by descending expected net edge.
    """
    out: List[CycleRecord] = []

    # Pre-index: pools by frozenset(sym0, sym1) for O(1) leg lookup
    by_pair: Dict[FrozenSet[str], List[Any]] = {
        frozenset(k.split("/")): v for k, v in pool_map.items()
    }

    # Geometric size grid (same design as _scan_triangular_cycles)
    grid: List[float] = []
    if grid_points > 1:
        ratio = max_trade_size_usd / min_size_usd
        grid = [
            min_size_usd * (ratio ** (i / (grid_points - 1)))
            for i in range(grid_points)
        ]
    else:
        grid = [max_trade_size_usd]

    for cycle_edges in graph.all_start_cycles(min_hops=min_hops, max_hops=max_hops):
        hop_count = len(cycle_edges)

        # Build concrete directed edges using the deepest pool per leg
        resolved: List[Optional[GraphEdge]] = []
        for edge in cycle_edges:
            pools = by_pair.get(frozenset((edge.from_token, edge.to_token)), [])
            best = best_pool_for_leg(edge.from_token, edge.to_token, pools)
            if best is None:
                break
            resolved.append(best)
        if len(resolved) != hop_count:
            continue  # missing pool for at least one leg

        start_token = cycle_edges[0].from_token
        price_start_usd = token_prices.get(start_token, 0.0)
        if price_start_usd <= 0:
            continue

        # Grid search: find the size that maximises net profit
        best_net = -math.inf
        best_size_usd = 0.0
        best_gross_usd = 0.0

        for size_usd in grid:
            x_in = size_usd / price_start_usd
            x_out = simulate_n_hop_cycle(resolved, x_in)
            if x_out <= 0.0:
                continue
            gross_usd = (x_out - x_in) * price_start_usd
            flash_fee = size_usd * flash_loan_fee_rate
            try:
                eip1559 = tip_optimizer.build_eip1559_params(
                    max(gross_usd - flash_fee, 0.01)
                )
            except Exception:
                continue
            gas_cost = eip1559["gas_cost_usd"] * gas_multiplier
            net = gross_usd - flash_fee - gas_cost
            if net > best_net:
                best_net = net
                best_size_usd = size_usd
                best_gross_usd = gross_usd

        if best_net < min_net_profit_usd or not math.isfinite(best_net):
            continue

        try:
            eip1559 = tip_optimizer.build_eip1559_params(max(best_net, 0.01))
        except Exception:
            continue
        gas_cost = eip1559["gas_cost_usd"] * gas_multiplier
        p_fill = eip1559["p_fill"]
        flash_fee = best_size_usd * flash_loan_fee_rate

        tok_seq = [e.from_token for e in resolved] + [start_token]
        cycle_label = "->".join(tok_seq)
        dex_chain = "->".join(e.pool.dex for e in resolved)

        out.append(CycleRecord(
            scan_no=scan_no,
            timestamp=time.time(),
            pair=cycle_label,
            buy_dex=dex_chain,
            sell_dex="multihop",
            buy_pool=resolved[0].pool.pool_address,
            sell_pool=resolved[-1].pool.pool_address,
            raw_spread_bps=round(
                10_000.0 * best_gross_usd / max(best_size_usd, 1.0), 4
            ),
            trade_size_usd=round(best_size_usd, 2),
            gross_profit_usd=round(best_gross_usd, 4),
            slippage_cost_usd=0.0,
            gas_cost_usd=round(gas_cost, 4),
            expected_net_edge=round(best_net, 4),
            p_fill=round(p_fill, 4),
            e_profit=round(best_net * p_fill if best_net > 0 else 0.0, 4),
            profitable=True,
            hop_count=hop_count,
        ))

    out.sort(key=lambda r: r.expected_net_edge, reverse=True)
    return out
