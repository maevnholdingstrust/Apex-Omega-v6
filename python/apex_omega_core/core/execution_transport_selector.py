from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionTransport:
    mode: str
    url: str


def select_execution_transport(chain_id: int = 137) -> ExecutionTransport:
    """Return the explicit write transport for a signed transaction.

    Polygon reads and writes intentionally use separate lanes. Public RPCs,
    DODO-discovered fallbacks, and Ethereum builder relays are never accepted
    as Polygon private-mempool RPC authority. Builder relays such as Titan are
    exposed as their own transport mode because they do not provide normal
    Polygon read methods like eth_chainId or eth_blockNumber.
    """
    if chain_id == 137:
        private_mempool = (
            os.getenv("POLYGON_PRIVATE_MEMPOOL_RPC_URL")
            or os.getenv("POLYGON_PRIVATE_MEMPOOL_URL")
            or ""
        ).strip()
        if private_mempool:
            return ExecutionTransport("polygon_private_mempool", private_mempool)
        titan = (os.getenv("TITAN_MEV_US_WEST") or os.getenv("TITAN_BUILDER_RPC_URL") or "").strip()
        if titan:
            return ExecutionTransport("titan_builder_bundle", titan)
        return ExecutionTransport("none", "")

    rpc = (os.getenv("ACTIVE_EXECUTION_RPC") or os.getenv("POLYGON_RPC_URL") or "").strip()
    if rpc:
        return ExecutionTransport("rpc", rpc)
    return ExecutionTransport("none", "")
