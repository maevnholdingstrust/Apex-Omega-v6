from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Spread:
    symbol: str
    bid: float
    ask: float
    timestamp: float

@dataclass
class Slippage:
    expected_price: float
    actual_price: float
    difference: float

@dataclass
class Feature:
    name: str
    value: float

@dataclass
class InferenceResult:
    net_edge: float
    features: List[Feature]

@dataclass
class ExecutionResult:
    success: bool
    slippage: Optional[Slippage] = None