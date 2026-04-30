"""Route Graph — directed pool-edge graph for arbitrage path enumeration.

Provides :class:`RouteGraph`, a lightweight directed multigraph where:

* **Nodes** are EIP-55 checksummed token addresses.
* **Edges** are pool swaps: one edge per (token_in, pool, token_out) direction.
  Because every AMM pool is bidirectional, :meth:`add_pool` inserts two
  directed edges (token0→token1 and token1→token0).

The main query methods are:

* :meth:`routes` — enumerate all paths from *src* to *dst* up to *max_hops*
  hops, returned as :class:`~.types.RouteSnapshot` objects.
* :meth:`arb_cycles` — enumerate all circular paths that start and end at the
  same *token* (A→…→A), which are the direct input to the C1/C2 arbitrage
  pipeline.

Both methods use a breadth-first search and respect ``max_hops`` to keep
enumeration bounded for large graphs.  They deduplicate paths at the pool-
address level so the same physical route is not returned twice regardless of
traversal order.

Usage::

    from apex_omega_core.core.route_graph import RouteGraph
    from apex_omega_core.core.types import PoolMeta

    pools = [PoolMeta(...), ...]
    graph = RouteGraph.from_pools(pools)

    # Two-hop routes from WMATIC to USDC
    routes = graph.routes(WMATIC, USDC, max_hops=2)

    # Arbitrage cycles starting from WMATIC
    cycles = graph.arb_cycles(WMATIC, max_hops=2)
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .types import PoolMeta, RouteHop, RouteSnapshot

# ---------------------------------------------------------------------------
# Address normalisation helper
# ---------------------------------------------------------------------------


def _normalise(address: str) -> str:
    """Lowercase-strip an EVM address for use as a graph key.

    We intentionally do *not* checksum here: the graph's internal keys are
    all lower-case so callers can pass either case without duplicating nodes.
    Full EIP-55 checksumming is expensive and not needed for graph traversal.
    The ``RouteSnapshot`` hops preserve whatever case was stored in the
    originating ``PoolMeta``.
    """
    addr = address.strip().lower()
    if not addr.startswith("0x"):
        addr = "0x" + addr
    return addr


# ---------------------------------------------------------------------------
# Internal edge type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Edge:
    """A directed swap edge in the route graph.

    ``token_in`` and ``token_out`` are lower-case.  ``pool_address``,
    ``dex_family``, ``fee_tier``, and ``pool_type`` are copied verbatim from
    the originating :class:`~.types.PoolMeta`.
    """

    pool_address: str
    token_in: str
    token_out: str
    fee_tier: float
    pool_type: str
    dex_family: str

    # The original token addresses (canonical case) are preserved for
    # ``RouteHop`` construction so downstream consumers get the checksum
    # address they provided when building the graph.
    token_in_raw: str
    token_out_raw: str
    pool_address_raw: str


# ---------------------------------------------------------------------------
# Route deduplication key
# ---------------------------------------------------------------------------


def _path_key(edges: List[_Edge]) -> str:
    """Stable deduplication key for a path: sorted pool addresses joined."""
    return "|".join(sorted(e.pool_address for e in edges))


# ---------------------------------------------------------------------------
# RouteGraph
# ---------------------------------------------------------------------------


class RouteGraph:
    """Directed multigraph of token-swap edges built from pool metadata.

    Parameters
    ----------
    min_tvl_usd:
        If positive, pools whose ``fee_tier`` metadata carries a TVL field
        below this threshold are skipped on :meth:`add_pool`.  **Note:** the
        standard :class:`~.types.PoolMeta` does not include TVL directly;
        pass ``min_tvl_usd=0.0`` (the default) to accept all pools.
    """

    def __init__(self, min_tvl_usd: float = 0.0) -> None:
        self._min_tvl_usd = min_tvl_usd
        # Lower-cased token address → outgoing edges.
        self._adj: Dict[str, List[_Edge]] = defaultdict(list)
        # Track pool addresses already inserted to prevent duplicate edges.
        self._seen_pools: Set[str] = set()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def add_pool(self, pool: PoolMeta) -> None:
        """Insert bidirectional swap edges for ``pool``.

        Duplicate pools (same ``pool.address``) are silently ignored so that
        callers can safely call this method multiple times with the same pool
        list.
        """
        pool_key = _normalise(pool.address)
        if pool_key in self._seen_pools:
            return
        self._seen_pools.add(pool_key)

        t0 = _normalise(pool.token0)
        t1 = _normalise(pool.token1)

        # Forward edge: token0 → token1
        edge_fwd = _Edge(
            pool_address=pool_key,
            token_in=t0,
            token_out=t1,
            fee_tier=pool.fee_tier,
            pool_type=pool.pool_type,
            dex_family=pool.dex_family,
            token_in_raw=pool.token0,
            token_out_raw=pool.token1,
            pool_address_raw=pool.address,
        )
        # Reverse edge: token1 → token0
        edge_rev = _Edge(
            pool_address=pool_key,
            token_in=t1,
            token_out=t0,
            fee_tier=pool.fee_tier,
            pool_type=pool.pool_type,
            dex_family=pool.dex_family,
            token_in_raw=pool.token1,
            token_out_raw=pool.token0,
            pool_address_raw=pool.address,
        )
        self._adj[t0].append(edge_fwd)
        self._adj[t1].append(edge_rev)

    def build_from_pools(self, pools: Iterable[PoolMeta]) -> None:
        """Add all pools in ``pools`` via :meth:`add_pool`."""
        for pool in pools:
            self.add_pool(pool)

    @classmethod
    def from_pools(
        cls,
        pools: Iterable[PoolMeta],
        min_tvl_usd: float = 0.0,
    ) -> "RouteGraph":
        """Construct a :class:`RouteGraph` from an iterable of pools."""
        graph = cls(min_tvl_usd=min_tvl_usd)
        graph.build_from_pools(pools)
        return graph

    # ------------------------------------------------------------------
    # Graph introspection
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        """Return the number of token nodes in the graph."""
        return len(self._adj)

    def edge_count(self) -> int:
        """Return the total number of directed edges (each pool = 2 edges)."""
        return sum(len(edges) for edges in self._adj.values())

    def pool_count(self) -> int:
        """Return the number of unique pools added to the graph."""
        return len(self._seen_pools)

    def neighbors(self, token: str) -> List[str]:
        """Return token addresses reachable in one hop from ``token``."""
        key = _normalise(token)
        return [e.token_out_raw for e in self._adj.get(key, [])]

    def has_token(self, token: str) -> bool:
        """Return ``True`` when ``token`` has at least one outgoing edge."""
        return _normalise(token) in self._adj

    # ------------------------------------------------------------------
    # Path enumeration
    # ------------------------------------------------------------------

    def routes(
        self,
        src: str,
        dst: str,
        max_hops: int = 2,
    ) -> List[RouteSnapshot]:
        """Return all non-repeating paths from ``src`` to ``dst``.

        Parameters
        ----------
        src:
            Input token address (any case).
        dst:
            Output token address (any case).
        max_hops:
            Maximum number of pool swaps in a path.  Values of 1 or 2 are
            typical; 3 or more are supported but may be slow on dense graphs.

        Returns
        -------
        list of :class:`~.types.RouteSnapshot`
            Each snapshot has ``is_valid=True``, ``min_input=0.0``,
            ``max_input=0.0``, and ``evaluation_block_number=0`` as
            placeholders; callers should populate those fields from live pool
            state before passing the route to C1.
        """
        src_key = _normalise(src)
        dst_key = _normalise(dst)

        if max_hops < 1:
            return []
        if src_key == dst_key:
            return []

        results: List[RouteSnapshot] = []
        seen_keys: Set[str] = set()

        # BFS queue: (current_node_key, path_so_far, visited_nodes)
        # visited_nodes prevents revisiting intermediate nodes.
        queue: deque[Tuple[str, List[_Edge], Set[str]]] = deque()
        queue.append((src_key, [], {src_key}))

        while queue:
            current, path, visited = queue.popleft()

            if len(path) > max_hops:
                continue

            for edge in self._adj.get(current, []):
                next_key = edge.token_out

                # Avoid revisiting any node we've already passed through,
                # UNLESS it is the destination (closes the path).
                if next_key in visited and next_key != dst_key:
                    continue

                new_path = path + [edge]

                if next_key == dst_key:
                    # We've reached the destination.
                    pk = _path_key(new_path)
                    if pk not in seen_keys:
                        seen_keys.add(pk)
                        results.append(self._build_snapshot(src, dst, new_path))
                    continue

                if len(new_path) < max_hops:
                    queue.append((next_key, new_path, visited | {next_key}))

        return results

    def arb_cycles(
        self,
        token: str,
        max_hops: int = 2,
    ) -> List[RouteSnapshot]:
        """Return all circular paths that start and end at ``token``.

        These are the canonical A→…→A arbitrage routes consumed by C1.  A
        minimum of 2 hops is required (otherwise no swap takes place).

        Parameters
        ----------
        token:
            The start/end token for the arbitrage cycle.
        max_hops:
            Maximum number of hops (pool swaps) in the cycle.  2 is the
            typical two-DEX case; 3 gives triangle arbitrage.
        """
        token_key = _normalise(token)

        if max_hops < 2:
            return []

        results: List[RouteSnapshot] = []
        seen_keys: Set[str] = set()

        # BFS queue: (current_node_key, path_so_far, visited_intermediate_nodes)
        queue: deque[Tuple[str, List[_Edge], Set[str]]] = deque()
        queue.append((token_key, [], set()))

        while queue:
            current, path, visited = queue.popleft()

            if len(path) > max_hops:
                continue

            for edge in self._adj.get(current, []):
                next_key = edge.token_out
                new_path = path + [edge]

                if next_key == token_key and len(new_path) >= 2:
                    # Cycle closed.
                    pk = _path_key(new_path)
                    if pk not in seen_keys:
                        seen_keys.add(pk)
                        results.append(
                            self._build_snapshot(token, token, new_path)
                        )
                    continue

                # Avoid revisiting intermediate nodes (not the origin token).
                if next_key in visited:
                    continue
                if next_key == token_key:
                    # Avoid leaving the origin again without taking any hops.
                    continue

                if len(new_path) < max_hops:
                    queue.append((next_key, new_path, visited | {next_key}))

        return results

    # ------------------------------------------------------------------
    # RouteSnapshot construction
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        src: str,
        dst: str,
        edges: List[_Edge],
    ) -> RouteSnapshot:
        """Convert a list of graph edges into a :class:`~.types.RouteSnapshot`."""
        hops = [
            RouteHop(
                pool_address=e.pool_address_raw,
                token_in=e.token_in_raw,
                token_out=e.token_out_raw,
                fee_tier=e.fee_tier,
                pool_type=e.pool_type,
            )
            for e in edges
        ]

        # Deterministic route_id: SHA-256 over ordered pool addresses.
        id_source = "|".join(e.pool_address for e in edges)
        route_id = hashlib.sha256(id_source.encode()).hexdigest()[:16]

        return RouteSnapshot(
            route_id=route_id,
            hops=hops,
            input_token=src,
            output_token=dst,
            min_input=0.0,
            max_input=0.0,
            evaluation_block_number=0,
            evaluation_timestamp_ms=int(time.time() * 1_000),
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RouteGraph("
            f"pools={self.pool_count()}, "
            f"nodes={self.node_count()}, "
            f"edges={self.edge_count()}"
            f")"
        )
