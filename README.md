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
- `core/spread_alignment.py` - BPS-native canonical layer
- `core/slippage_sentinel.py` - Multi-protocol routing engine
- `core/inference.py` - Net edge derivation (no double-count)
- `core/feature_factory.py` - Deterministic feature extraction
- `core/spread_engine.c` - BPS-native C layer
- `core/types.py` - Shared dataclasses

### Strategies
- `strategies/c1_aggressor_apex.py` - Full-throttle execution
- `strategies/c2_surgeon_apex.py` - Surgical precision routing
- `strategies/execution_router.py` - Smart decision engine

### Operations
- `operations/validate_spread_alignment.py` - Alignment verification

### Tests
- `tests/test_spread_alignment.py` - Unit tests for BPS conversions
- `tests/test_slippage_sentinel.py` - Protocol slippage validation
- `tests/test_feature_factory.py` - Feature extraction tests
- `tests/test_integration.py` - End-to-end route ranking
- `tests/conftest.py` - Pytest fixtures

## Usage

### Basic Example

```python
from core.spread_alignment import align_spread
from core.types import Spread

spread = Spread(symbol='EURUSD', bid=1.1000, ask=1.1005, timestamp=1234567890.0)
aligned = align_spread(spread)
print(f"Aligned spread: {aligned}")
```

### Running Tests

```bash
pytest tests/
```

### Dry Run

Run the dry run script to exercise all components and measure performance:

```bash
python dry_run.py
```

This will output detailed logs and timing for each module.

## Contributing

Please read the contributing guidelines before submitting pull requests.

## License

This project is licensed under the MIT License.