# Global Math And BPS Reference

Authoritative standard for all future on-chain and off-chain builds in this repository.

## Scope

This document is the single source of truth for:

- Basis point (BPS) math.
- Fixed-point scaling conventions.
- Protocol-specific fee/parameter scales.
- Discovery verification requirements before opportunity math.

If any code, config, or external note conflicts with this file, this file wins.

## Non-Negotiable Units

- `1 bps = 0.01% = 0.0001 = 1 / 10_000`
- `100 bps = 1%`
- `10_000 bps = 100%`
- `WAD = 1e18` (18-decimal fixed-point)

Canonical fee formula:

```text
fee_amount = amount * fee_bps / 10_000
```

Canonical ratio conversions:

```text
ratio = bps / 10_000
bps   = ratio * 10_000
```

Example:

- `0.3% = 30 bps`
- `fee = amount * 30 / 10_000`

## Current Canonical Implementations In This Repo

- Python BPS utilities: `python/apex_omega_core/core/spread_alignment.py`
- Rust BPS scale: `src/lib.rs`
- Solidity executor constants/helper: `contracts/InstitutionalExecutor.sol`
- Discovery verification gates: `python/apex_omega_core/core/slippage_sentinel.py`

Do not create alternate BPS helpers in new modules unless required by language/runtime constraints.

## Protocol-Specific Scales (Verified)

Use these exactly as documented by protocol source contracts.

### Uniswap V3 / Algebra-style Fee Tier Encoding

- `fee()` returns hundredths of a bps.
- `3000` means `30 bps` (`0.3%`).
- Normalize with:

```text
fee_bps = fee_tier_raw / 100
```

### Balancer V3

- Swap fee percentages are 18-decimal fixed-point values.
- Weighted pools:
  - min swap fee percentage: `0.001e16` (0.001%)
  - max swap fee percentage: `10e16` (10%)
  - minimum token weight: `1e16` (1%)
  - normalized weights must sum to `1e18`
- Stable pools:
  - min swap fee percentage: `1e12` (0.0001%)
  - max swap fee percentage: `10e16` (10%)

### Curve StableSwap-NG

- `FEE_DENOMINATOR = 1e10`
- `fee` and `offpeg_fee_multiplier` use `1e10` precision.
- `MAX_FEE = 5e9`
- `A_PRECISION = 100`
- `MAX_A = 1e6`

## Discovery-First Verification Rule

No off-chain inventory row is allowed to influence opportunity math unless discovery verification passes.

Required gate behavior:

- Discovery must mark row as verified.
- Fee must be verified by normalized source or an accepted protocol-default rule.
- Unverified rows must be rejected and counted in diagnostics.

Current enforcement knobs:

- `DISCOVERY_REQUIRE_VERIFIED=true`
- `DISCOVERY_REQUIRE_FEE_VERIFIED=true`

## Anti-Drift Rules For New Builds

Every new build must satisfy all rules below.

1. Do not use `1_000` as a percent/bps denominator.
2. Do not hardcode fee math with ad-hoc constants.
3. Convert protocol-native fee representations into canonical `fee_bps` before route math.
4. Keep all policy thresholds in bps and document unit at declaration.
5. Preserve deterministic rounding policy per language.
6. Run discovery verification before ranking/scoring/execution decisions.
7. Include diagnostics for rejected data and rejected routes.

## Build Checklist

Before merge/deploy:

1. Confirm BPS denominator is `10_000` for all bps math paths.
2. Confirm protocol fee decoding is correct for each DEX type.
3. Confirm discovery verification gates are enabled for production.
4. Confirm unit tests include:
   - `30 bps` fee case.
   - Uniswap V3 tier decode (`3000 -> 30 bps`).
   - Balancer scale handling (`1e18` fee percentages).
   - Curve scale handling (`1e10` fee denominator).
5. Confirm route diagnostics expose reject reasons.

## Source References

- Balancer WeightedPool:
  - https://github.com/balancer/balancer-v3-monorepo/blob/main/pkg/pool-weighted/contracts/WeightedPool.sol
- Balancer StablePool:
  - https://github.com/balancer/balancer-v3-monorepo/blob/main/pkg/pool-stable/contracts/StablePool.sol
- Curve StableSwap-NG:
  - https://github.com/curvefi/stableswap-ng/blob/main/contracts/main/CurveStableSwapNG.vy
