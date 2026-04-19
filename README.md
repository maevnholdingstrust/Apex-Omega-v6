# Apex-Omega-v6

A comprehensive trading system for spread alignment, slippage management, and execution strategies.

## Overview

Apex-Omega-v6 is a high-performance trading platform that provides:

- BPS-native spread alignment and canonical layers
- Multi-protocol routing with slippage sentinel
- Net edge derivation without double-counting
- Deterministic feature extraction
- Full-throttle and surgical precision execution strategies
- Complete validation and testing suite

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/maevnholdingstrust/Apex-Omega-v6.git
   cd Apex-Omega-v6
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Project Structure

### Core Modules
- `python/apex_omega_core/core/spread_alignment.py` - BPS-native canonical layer
- `python/apex_omega_core/core/slippage_sentinel.py` - Multi-protocol routing engine
- `python/apex_omega_core/core/inference.py` - Net edge derivation (no double-count)
- `python/apex_omega_core/core/feature_factory.py` - Deterministic feature extraction
- `python/apex_omega_core/core/types.py` - Shared dataclasses

### Strategies
- `python/apex_omega_core/strategies/c1_aggressor_apex.py` - Full-throttle execution
- `python/apex_omega_core/strategies/c2_surgeon_apex.py` - Surgical precision routing
- `python/apex_omega_core/strategies/execution_router.py` - Smart decision engine

### Operations
- `python/apex_omega_core/operations/validate_spread_alignment.py` - Alignment verification

### Tests
- `python/apex_omega_core/tests/test_spread_alignment.py` - Unit tests for BPS conversions
- `python/apex_omega_core/tests/test_slippage_sentinel.py` - Protocol slippage validation
- `python/apex_omega_core/tests/test_feature_factory.py` - Feature extraction tests
- `python/apex_omega_core/tests/test_integration.py` - End-to-end route ranking
- `python/apex_omega_core/tests/conftest.py` - Pytest fixtures

## Usage

### Basic Example

```python
from apex_omega_core.core.spread_alignment import align_spread
from apex_omega_core.core.types import Spread

spread = Spread(symbol='EURUSD', bid=1.1000, ask=1.1005, timestamp=1234567890.0)
aligned = align_spread(spread)
print(f"Aligned spread: {aligned}")
```

### Running Tests

```bash
pytest python/apex_omega_core/tests/
```

### Dry Run

Run the dry run script to exercise all components and measure performance:

```bash
python python/dry_run.py
```

This will output detailed logs and timing for each module.

## Contributing

Please read the contributing guidelines before submitting pull requests.

## License

This project is licensed under the MIT License.
