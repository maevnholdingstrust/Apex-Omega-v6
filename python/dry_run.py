#!/usr/bin/env python3
"""
Dry run script for Apex-Omega-v6 Polygon arbitrage system.
Exercises core components and measures performance.
"""

import asyncio
import time
import logging
from apex_omega_core.core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.inference import derive_net_edge
from apex_omega_core.core.feature_factory import extract_features
from apex_omega_core.strategies.execution_router import ExecutionRouter
from apex_omega_core.operations.validate_spread_alignment import validate_spread_alignment
from apex_omega_core.core.types import Spread, ArbitrageOpportunity, Pool, FlashLoanConfig
from apex_omega_core.core.polygon_arbitrage import PolygonDEXMonitor, ArbitrageDetector

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Apex-Omega-v6 Polygon Arbitrage Dry Run")

    # Sample data
    spread = Spread(symbol='USDC', bid=1.0000, ask=1.0005, timestamp=time.time())
    data = {'edge': 0.05, 'price': 100.0, 'volume': 1000}

    # Test spread alignment
    logger.info("Testing Spread Alignment")
    start = time.time()
    aligned = align_spread(spread)
    end = time.time()
    print(f"Original: bid={spread.bid}, ask={spread.ask}")
    print(f"Aligned: bid={aligned.bid}, ask={aligned.ask}")
    logger.info(f"Spread alignment completed in {end - start:.6f}s")

    # BPS conversions
    logger.info("Testing BPS Conversions")
    start = time.time()
    bps = decimal_to_bps(0.01)
    decimal = bps_to_decimal(bps)
    end = time.time()
    print(f"0.01 to BPS: {bps}, back to decimal: {decimal}")
    logger.info(f"BPS conversions completed in {end - start:.6f}s")

    # Slippage sentinel with DEX routing
    logger.info("Testing DEX-Aware Slippage Sentinel")
    sentinel = SlippageSentinel()
    start = time.time()
    protocol = sentinel.route(data, ['uniswap', 'sushiswap'])
    # Mock arbitrage opportunity
    buy_pool = Pool("0x123", "uniswap", "USDC", "WETH", 1000000, 0.003)
    sell_pool = Pool("0x456", "sushiswap", "USDC", "WETH", 800000, 0.003)
    mock_opp = ArbitrageOpportunity(
        token="USDC",
        buy_pool=buy_pool,
        sell_pool=sell_pool,
        buy_price=1.0,
        sell_price=1.005,
        spread_bps=50,
        estimated_profit_usd=250,
        flash_loan_amount=50000,
        flash_loan_token="USDC",
        path=["0x123", "0x456"],
        gas_estimate=0.1
    )
    flash_size = sentinel.calculate_flash_loan_size(mock_opp)
    end = time.time()
    print(f"Routed DEX: {protocol}")
    print(f"Flash loan size for opportunity: ${flash_size:,.0f}")
    logger.info(f"Slippage sentinel completed in {end - start:.6f}s")

    # Inference
    logger.info("Testing Inference")
    start = time.time()
    result = derive_net_edge(data)
    end = time.time()
    print(f"Net edge: {result.net_edge}")
    print(f"Features: {[f'{f.name}: {f.value}' for f in result.features]}")
    logger.info(f"Inference completed in {end - start:.6f}s")

    # Feature extraction
    logger.info("Testing Feature Extraction")
    start = time.time()
    features = extract_features(data)
    end = time.time()
    print(f"Extracted features: {[f'{f.name}: {f.value}' for f in features]}")
    logger.info(f"Feature extraction completed in {end - start:.6f}s")

    # Arbitrage detection
    logger.info("Testing Arbitrage Detection")
    dex_monitor = PolygonDEXMonitor()
    detector = ArbitrageDetector(dex_monitor, FlashLoanConfig())
    tokens = ["0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"]  # USDC
    start = time.time()
    opportunities = await detector.find_opportunities(tokens)
    end = time.time()
    print(f"Found {len(opportunities)} arbitrage opportunities")
    if opportunities:
        opp = opportunities[0]
        print(f"Sample opportunity: {opp.token} spread {opp.spread_bps}bps, profit ${opp.estimated_profit_usd:.2f}")
    logger.info(f"Arbitrage detection completed in {end - start:.6f}s")

    # Execution router with arbitrage
    logger.info("Testing Arbitrage Execution Router")
    router = ExecutionRouter()
    start = time.time()
    if opportunities:
        exec_result = await router.execute_arbitrage(opportunities[0])
        print(f"Arbitrage execution success: {exec_result.success}")
        if exec_result.tx_hash:
            print(f"Transaction hash: {exec_result.tx_hash}")
    else:
        print("No opportunities to execute")
    end = time.time()
    logger.info(f"Execution router completed in {end - start:.6f}s")

    # Validation
    logger.info("Testing Validation")
    start = time.time()
    valid = validate_spread_alignment(spread)
    end = time.time()
    print(f"Alignment valid: {valid}")
    logger.info(f"Validation completed in {end - start:.6f}s")

    logger.info("Polygon Arbitrage Dry Run Complete")

if __name__ == "__main__":
    asyncio.run(main())