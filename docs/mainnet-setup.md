# Mainnet Execution — Setup Guide

This document explains every GitHub setting that must be configured **outside the
repository files** to enable controlled, gated live execution on Polygon mainnet.

## Architecture overview

| Trigger | Workflow | Environment | Effect |
|---------|----------|-------------|--------|
| PR / push to any branch | `ci.yml` | *(none)* | Compile + unit tests. No secrets. |
| Push to `main` | `integration.yml` | `dev` | Live data dry-run. `APEX_SEND_TX=0`. No tx broadcast. |
| Manual (`workflow_dispatch`) | `prod.yml` | `prod` | **Live mainnet execution.** Requires `EXECUTE` confirmation input **plus** environment reviewer approval. |

`prod` is the mainnet environment (Polygon mainnet, chain ID 137).

---

## 1 — Create the GitHub Actions environments

### `dev` environment
1. Go to **Settings → Environments → New environment**.
2. Name: `dev`.
3. No required reviewers (CI automation — low risk).
4. Add the following **secrets**:
   - `POLYGON_RPC_URL` — HTTP RPC endpoint (e.g. `https://polygon-rpc.com`)
   - `POLYGON_WSS_URL` — WebSocket RPC endpoint
   - `PRIVATE_KEY` — wallet private key (used only to build signed calldata; tx not broadcast)
   - `RELAY_AUTH_KEY` — MEV relay authentication key
   - `POLYGONSCAN_API_KEY` — PolygonScan API key
5. Add the following **variables**:
   - `CHAIN_ID` = `137`
   - `NETWORK` = `polygon`
   - `EXECUTOR_C1_ADDRESS` — deployed C1 contract address
   - `EXECUTOR_C2_ADDRESS` — deployed C2 contract address
   - `ROUTER_QUICKSWAP` — QuickSwap router address
   - `ROUTER_UNISWAP_V3` — Uniswap V3 router address
   - `SAFE_GAS_MULTIPLIER` = `1.2` (or your preferred multiplier)
   - `ENABLE_BUNDLE_MODE` = `0` (set to `1` to enable MEV bundle submission)

### `prod` environment
1. Go to **Settings → Environments → New environment**.
2. Name: `prod`.
3. **Required reviewers**: add at least one trusted maintainer.
   - This creates a mandatory approval gate before any job targeting `prod` can run.
4. **Deployment branches**: restrict to `main` only.
5. Add the **same secrets and variables** as `dev` but pointing at production-grade
   RPC endpoints and the live private key.

> **Security note**: the `PRIVATE_KEY` stored in `prod` is the hot wallet used to sign
> and broadcast mainnet transactions.  Use a dedicated execution wallet with only the
> minimum MATIC/POL required for gas.  Never reuse a key that holds long-term funds.

---

## 2 — Protect the `main` branch

Go to **Settings → Branches → Add branch protection rule** (or create a ruleset).

Minimum recommended settings for `main`:

| Setting | Value |
|---------|-------|
| Require a pull request before merging | ✅ enabled |
| Required approvals | 1 (or more) |
| Dismiss stale pull request approvals when new commits are pushed | ✅ recommended |
| Require status checks to pass before merging | ✅ enabled |
| Required status checks | `Python lint & tests` (from `ci.yml`) |
| Require branches to be up to date before merging | ✅ recommended |
| Restrict who can push to matching branches | maintainers only |
| Do not allow bypassing the above settings | ✅ recommended |

Optional but strongly recommended:

- **Require signed commits** — ensures all commits on `main` are GPG/SSH signed.
- **Require conversation resolution before merging** — forces review threads to be resolved.

---

## 3 — Triggering a live production run

After the `prod` environment and branch protection are configured:

1. Go to **Actions → prod → Run workflow**.
2. In the `confirm` input, type exactly: `EXECUTE`
3. Click **Run workflow**.
4. A reviewer listed in the `prod` environment will receive an approval request.
5. After approval, the job checks out the code, builds the Rust extension wheel
   via `maturin`, installs all Python dependencies, and starts
   `python/polygon_arbitrage_bot.py` using the secrets from the `prod` environment.

Execution can be stopped at any time by cancelling the workflow run from the Actions UI.

---

## 4 — Secrets never committed to the repository

The following values must **only** live in GitHub environment secrets:

- `POLYGON_RPC_URL` / `POLYGON_WSS_URL` — paid RPC credentials
- `PRIVATE_KEY` — execution wallet signing key
- `RELAY_AUTH_KEY` — MEV relay auth token
- `POLYGONSCAN_API_KEY` — block explorer API key

Non-sensitive configuration (addresses, flags, chain ID) is stored as **repository
or environment variables**, not secrets, so they are visible in workflow logs and
can be audited without revealing credentials.
