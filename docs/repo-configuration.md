# Repository configuration

This repository uses a split configuration model:

- **GitHub Actions Variables** for non-sensitive values.
- **GitHub Actions Secrets** for private credentials.
- **Local `.env` override files** for developer machines only.

## 1) GitHub repository/environment variables (non-sensitive)

Configure these in repository or environment variables:

- `CHAIN_ID`
- `NETWORK`
- `EXECUTOR_C1_ADDRESS`
- `EXECUTOR_C2_ADDRESS`
- `C1_INSTITUTIONAL_EXECUTOR_ADDRESS`
- `C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS`
- `ROUTER_QUICKSWAP`
- `ROUTER_UNISWAP_V3`
- `SAFE_GAS_MULTIPLIER`
- `ENABLE_BUNDLE_MODE`

## 2) GitHub repository/environment secrets (sensitive)

Configure these as secrets:

- `POLYGON_RPC_URL`
- `POLYGON_WSS_URL`
- `PRIVATE_KEY`
- `EXECUTOR_PRIVATE_KEY`
- `RELAY_AUTH_KEY`
- `POLYGONSCAN_API_KEY`

## 3) Environment separation

Create GitHub environments:

- `dev` - integration dry-runs and validation
- `staging` - pre-production validation
- `prod` - protected live execution

## 4) Local setup

1. `python/apex_omega_core/.env` is a committed sanitized template; copy it to `python/apex_omega_core/.env.local` for machine-specific overrides.
2. Edit only the values you actually need in your local override file.
3. Keep `APEX_SEND_TX=0` unless intentionally running live execution.
4. Price baselines should come from live on-chain / market data feeds at runtime; avoid static local USD overrides for routing decisions.
5. Use `C1_TARGET` and `C2_TARGET` as the canonical flashloan-capable execution targets. Do not collapse them into a single `FLASHLOAN_EXECUTOR_ADDRESS`.
6. Keep `ROUTE_STEP_DATA_HEX=` empty in production configuration. It is fallback/debug only.
7. Use `ROUTE_STEP_DATA_SOURCE=GENERATED` with `ROUTER_CALLDATA_GENERATORS_ENABLED=true` so every `RouteStep.data` is populated by the router calldata generator before route envelope encoding.
8. Never commit real API keys or private keys.

## 5) Execution payload rule

Production execution is strictly mechanical:

`C1 selected route -> router calldata generator -> RouteStep.data filled -> RouteEnvelope built -> ABI encoded -> initAaveFlash(asset, amount, minProfit, payload) -> fork simulation -> live/bundle only after simulation passes`

No generated calldata means no `RouteStep`, no `RouteEnvelope`, and no live execution.

## 6) CI behavior summary

- `ci.yml` is safe for pull requests and does not require secrets.
- `integration.yml` uses the `dev` environment and forces dry-run mode.
- `prod.yml` is manual-dispatch only and requires explicit confirmation.
