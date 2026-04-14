from typing import List
from .types import Slippage

class SlippageSentinel:
    """Multi-protocol routing engine."""

    def __init__(self):
        self.protocols = ["http", "tcp", "udp"]

    def route(self, data: dict, protocols: List[str]) -> str:
        """Route based on available protocols."""
        for protocol in protocols:
            if protocol in self.protocols:
                return protocol
        return "default"

    def calculate_slippage(self, expected: float, actual: float) -> Slippage:
        """Calculate slippage."""
        diff = actual - expected
        return Slippage(expected_price=expected, actual_price=actual, difference=diff)