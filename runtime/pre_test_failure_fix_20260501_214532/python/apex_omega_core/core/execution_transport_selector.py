from __future__ import annotations
import os
from dataclasses import dataclass
@dataclass(frozen=True)
class ExecutionTransport:
    mode: str
    url: str
def select_execution_transport(prefer_relay=True):
    relay = os.getenv("ACTIVE_PRIVATE_RELAY")
    rpc = os.getenv("ACTIVE_EXECUTION_RPC") or os.getenv("POLYGON_RPC_URL")
    if prefer_relay and relay: return ExecutionTransport("relay", relay)
    if rpc: return ExecutionTransport("rpc", rpc)
    return ExecutionTransport("none", "")
