# Repository configuration

This repository uses a split configuration model:

- **GitHub Actions Variables** for non-sensitive values.
- **GitHub Actions Secrets** for private credentials.
- **Local `.env` files** for developer machines only.

## 1) GitHub repository/environment variables (non-sensitive)

Configure these in repository or environment variables:

- `CHAIN_ID`
- `NETWORK`
- `EXECUTOR_C1_ADDRESS`
- `EXECUTOR_C2_ADDRESS`
- `ROUTER_QUICKSWAP`
- `ROUTER_UNISWAP_V3`
- `SAFE_GAS_MULTIPLIER`
- `ENABLE_BUNDLE_MODE`

## 2) GitHub repository/environment secrets (sensitive)

Configure these as secrets:

- `POLYGON_RPC_URL`
- `POLYGON_WSS_URL`
- `PRIVATE_KEY`
- `RELAY_AUTH_KEY`
- `POLYGONSCAN_API_KEY`

## 3) Environment separation

Create GitHub environments:

- `dev` — integration dry-runs and validation
- `staging` — pre-production validation
- `prod` — protected live execution

## 4) Local setup

1. Copy `python/apex_omega_core/.env` and fill in only local values.
2. Keep `APEX_SEND_TX=0` unless intentionally running live execution.
3. Never commit real API keys or private keys.

## 5) CI behavior summary

- `ci.yml` is safe for pull requests and does not require secrets.
- `integration.yml` uses the `dev` environment and forces dry-run mode.
- `prod.yml` is manual-dispatch only and requires explicit confirmation.
