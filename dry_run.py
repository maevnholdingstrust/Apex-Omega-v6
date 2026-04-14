#!/usr/bin/env python3
"""
Dry run script for Apex-Omega-v6 system.
Exercises core components and measures performance.
"""

import time
import logging
from core.spread_alignment import align_spread, bps_to_decimal, decimal_to_bps
from core.slippage_sentinel import SlippageSentinel
from core.inference import derive_net_edge
from core.feature_factory import extract_features
from strategies.execution_router import ExecutionRouter
from operations.validate_spread_alignment import validate_spread_alignment
from core.types import Spread

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Apex-Omega-v6 Dry Run")

    # Sample data
    spread = Spread(symbol='EURUSD', bid=1.1000, ask=1.1005, timestamp=time.time())
    order = {'price': 1.1000}
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

    # Slippage sentinel
    logger.info("Testing Slippage Sentinel")
    sentinel = SlippageSentinel()
    start = time.time()
    protocol = sentinel.route(data, ['http', 'tcp'])
    slippage = sentinel.calculate_slippage(100.0, 101.0)
    end = time.time()
    print(f"Routed protocol: {protocol}")
    print(f"Slippage: expected={slippage.expected_price}, actual={slippage.actual_price}, diff={slippage.difference}")
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

    # Execution router
    logger.info("Testing Execution Router")
    router = ExecutionRouter()
    start = time.time()
    exec_result = router.route(order, 'surgeon')
    end = time.time()
    print(f"Execution success: {exec_result.success}")
    if exec_result.slippage:
        print(f"Slippage: {exec_result.slippage.difference}")
    logger.info(f"Execution router completed in {end - start:.6f}s")

    # Validation
    logger.info("Testing Validation")
    start = time.time()
    valid = validate_spread_alignment(spread)
    end = time.time()
    print(f"Alignment valid: {valid}")
    logger.info(f"Validation completed in {end - start:.6f}s")

    logger.info("Dry Run Complete")

if __name__ == "__main__":
    main()