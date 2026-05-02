from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict

from web3 import Web3


@dataclass
class LaneState:
    lane_id: int
    next_nonce: int | None = None
    active: bool = False
    last_tx_hash: str | None = None
    metadata: dict = field(default_factory=dict)


class NonceLaneManager:
    """Thread-safe nonce allocator for multi-lane execution.

    Canonical use:
    - 32 lanes available for candidate/payload execution.
    - Each lane receives a stable nonce reservation.
    - C1/C2 can be correlated by lane_id and opportunity_id.
    """

    def __init__(self, w3: Web3, address: str, lane_count: int = 32):
        if lane_count <= 0:
            raise ValueError("lane_count must be positive")
        self.w3 = w3
        self.address = address
        self.lane_count = lane_count
        self._lock = threading.Lock()
        self._lanes: Dict[int, LaneState] = {i: LaneState(i) for i in range(lane_count)}
        self._base_nonce: int | None = None
        self._cursor = 0

    def sync(self) -> None:
        with self._lock:
            self._base_nonce = self.w3.eth.get_transaction_count(self.address, "pending")
            for offset, lane in self._lanes.items():
                lane.next_nonce = self._base_nonce + offset

    def reserve_lane(self, opportunity_id: str | None = None) -> LaneState:
        with self._lock:
            if self._base_nonce is None:
                self._base_nonce = self.w3.eth.get_transaction_count(self.address, "pending")
                for offset, lane in self._lanes.items():
                    lane.next_nonce = self._base_nonce + offset

            for _ in range(self.lane_count):
                lane = self._lanes[self._cursor]
                self._cursor = (self._cursor + 1) % self.lane_count
                if not lane.active:
                    lane.active = True
                    if opportunity_id is not None:
                        lane.metadata["opportunity_id"] = opportunity_id
                    return lane
            raise RuntimeError("No nonce lanes available")

    def mark_submitted(self, lane_id: int, tx_hash: str) -> None:
        with self._lock:
            lane = self._lanes[lane_id]
            lane.last_tx_hash = tx_hash

    def release_lane(self, lane_id: int) -> None:
        with self._lock:
            lane = self._lanes[lane_id]
            lane.active = False
            lane.metadata.clear()
            if lane.next_nonce is not None:
                lane.next_nonce += self.lane_count

    def snapshot(self) -> dict[int, dict]:
        with self._lock:
            return {
                lane_id: {
                    "next_nonce": lane.next_nonce,
                    "active": lane.active,
                    "last_tx_hash": lane.last_tx_hash,
                    "metadata": dict(lane.metadata),
                }
                for lane_id, lane in self._lanes.items()
            }
