from dataclasses import dataclass
from typing import List, Optional, Dict, Any

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
    tx_hash: Optional[str] = None

@dataclass
class Pool:
    address: str
    dex: str
    token0: str
    token1: str
    tvl_usd: float
    fee: float

@dataclass
class ArbitrageOpportunity:
    token: str
    buy_pool: Pool
    sell_pool: Pool
    buy_price: float
    sell_price: float
    spread_bps: float
    estimated_profit_usd: float
    flash_loan_amount: float
    flash_loan_token: str
    path: List[str]  # Up to 4 hops
    gas_estimate: float

@dataclass
class FlashLoanConfig:
    min_amount_usd: float = 5000.0
    max_pool_tvl_percent: float = 0.1  # 10%
    supported_providers: List[str] = None

    def __post_init__(self):
        if self.supported_providers is None:
            self.supported_providers = ['aave', 'balancer', 'uniswap']