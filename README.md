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

## One-Click Boot

> **TL;DR — start the full system in a single command:**
>
> ```bash
> ./start.sh
> ```

`start.sh` automates every step from a clean checkout to a fully-running system:

| Step | What happens |
|------|-------------|
| 1 | Loads `python/apex_omega_core/.env` (shell vars take precedence — never hardcoded) |
| 2 | Installs Python dependencies (`requirements.txt`) |
| 3 | Builds the Rust/PyO3 extension wheel via `maturin` and installs it |
| 4 | Verifies all core Python modules import cleanly |
| 5 | Starts the Flask dashboard server on **port 5000** (background) |
| 6 | Starts the Polygon arbitrage bot (foreground — `Ctrl+C` shuts everything down) |

**Optional flags:**

```bash
./start.sh --dry-run         # Force shadow mode (LIVE_EXECUTION=false, no TX broadcast)
./start.sh --dashboard-only  # Dashboard only — no bot process
./start.sh --bot-only        # Bot only — no dashboard
./start.sh --no-build        # Skip Rust build when a wheel is already cached
```

**Stopping the system:**

```bash
./stop.sh          # graceful SIGTERM to all tracked processes
# — or —
Ctrl+C             # also triggers graceful shutdown
```

**Logs:** `dashboard.log` and `bot.log` are written to the repo root.

> ⚠️ **Security:** `LIVE_EXECUTION` defaults to `false`. Set it to `true` in your `.env`
> only after verifying your configuration. The `prod` GitHub Actions environment requires
> manual approval before any live execution runs on CI.

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
- `python/apex_omega_core/core/ssot_pipeline.py` - Full-stack SSOT 2-leg arb pipeline;
  `c_total_exec = flash_fee + gas_cost` (DEX fees are embedded in AMM outputs, never double-counted)

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

## Build Artifacts

The `target/` directory (produced by Cargo / maturin when building the Rust
extension) is excluded from version control via `.gitignore`.  It is
regenerated automatically on every local build and CI run.

See [`docs/build-artifacts.md`](docs/build-artifacts.md) for a full
explanation of what lives under `target/`, what `target/.rustc_info.json` is,
and developer guidance.

Repository setup and environment configuration are documented in
[`docs/repo-configuration.md`](docs/repo-configuration.md).

The full pipeline symbol and math map is documented in
[`docs/PIPELINE_STAGE_FUNCTION_VARIABLE_MATH_INDEX.md`](docs/PIPELINE_STAGE_FUNCTION_VARIABLE_MATH_INDEX.md).

## Contributing

Please read the contributing guidelines before submitting pull requests.

## License

This project is licensed under the MIT License.
