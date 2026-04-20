# Apex-Omega Repository Runtime + Secret Policy

## Operating mode
This repository must be safe for:
- local development
- internal CI
- forked pull requests
- privileged integration testing
- protected production execution

The agent must never assume secrets are available in forked PRs.

---

## Secret handling rules

### Use GitHub Actions Variables for non-sensitive config
Store these as repository variables:
- `CHAIN_ID`
- `NETWORK`
- `EXECUTOR_C1_ADDRESS`
- `EXECUTOR_C2_ADDRESS`
- `ROUTER_QUICKSWAP`
- `ROUTER_UNISWAP_V3`
- `SAFE_GAS_MULTIPLIER`
- `ENABLE_BUNDLE_MODE`

### Use GitHub Actions Secrets for sensitive values
Store these as repository or environment secrets:
- `POLYGON_RPC_URL`
- `POLYGON_WSS_URL`
- `PRIVATE_KEY`
- `RELAY_AUTH_KEY`
- `POLYGONSCAN_API_KEY`
- any paid RPC/API credentials
- any bundle / relay signing credentials

### Environment separation
Use these GitHub environments:
- `dev`
- `staging`
- `prod`

Rules:
- `dev` = low-risk integration and dry-run testing
- `staging` = mainnet-read or near-production validation without production signer
- `prod` = protected execution only, with approval gates

---

## Fork safety rules

### Pull requests from forks
Fork PR workflows must:
- run without secrets
- avoid privileged deployment steps
- avoid relay submission
- avoid real signing
- avoid production RPC dependencies unless explicitly public and safe

Allowed in fork PRs:
- formatting
- linting
- static analysis
- unit tests
- compile/build checks
- mock or deterministic local simulation tests

Disallowed in fork PRs:
- real private keys
- environment secrets
- write tokens
- relay submission
- production contract mutation
- self-hosted privileged runners

---

## Workflow intent

### `pull_request`
Safe CI only.
No secrets.
No privileged steps.

### `push`
Internal branch workflows may use `dev` or `staging` environment secrets.

### `workflow_dispatch`
Used for maintainer-triggered integration or fork validation after review.

### `prod`
Only protected branches and approved maintainers may run production jobs.

---

## Agent behavior requirements

When generating or modifying workflows, always enforce:

1. Minimal `GITHUB_TOKEN` permissions
2. No secrets in `pull_request` jobs
3. Environment-gated privileged jobs
4. Clear separation between:
   - safe CI
   - integration testing
   - deployment / live execution
5. No hardcoded credentials
6. No direct logging of secret-derived values
7. No automatic use of production signer in generic CI
8. No assumption that C1/C2 execution should run live during PR validation

---

## Preferred CI structure

### Safe CI job
Purpose:
- compile
- lint
- unit test
- validate config parsing
- validate route-building deterministically

Must not require secrets.

### Internal integration job
Purpose:
- dry-run against dev or staging environment
- optional fork-state simulation
- off-chain pipeline validation
- non-production signing only if explicitly environment-gated

### Production job
Purpose:
- protected execution path only
- manual approval or strict branch restriction
- production secrets only through `prod` environment

---

## Security constraints for generated workflows

The agent must:
- default `permissions` to read-only unless a wider scope is truly required
- prefer official GitHub actions where possible
- pin third-party actions tightly when added
- avoid `pull_request_target` unless explicitly justified and safely designed
- avoid exposing secrets through artifacts, logs, test outputs, or echo statements
- keep self-hosted runner usage restricted away from untrusted fork code

---

## Copilot coding agent network allowlist guidance

If external access is required, only use explicitly allowlisted domains.
Typical examples for this repo may include:
- Polygon RPC domain
- relay/builder domain
- block explorer API domain
- strictly necessary package/documentation domains

The agent must keep external network dependency minimal.

---

## Apex-Omega specific execution policy

This repository contains an adversarial trading system.
Generated code and workflows must preserve:

- C1 = Aggressor
- C2 = Surgeon
- C1 and C2 are the only decision authorities
- execution plumbing is mechanical only
- no hidden role drift
- no secret leakage
- no live trading on untrusted CI paths
- no production mutation from fork PRs

Any live execution path must be explicitly environment-gated and disabled by default outside protected internal workflows.

---

## Default workflow generation pattern

When creating GitHub Actions:
- safe CI on `pull_request`
- privileged integration on `push` or `workflow_dispatch`
- production only through protected environment approvals

Do not collapse all behaviors into a single always-privileged workflow.
Keep safe and privileged jobs clearly separated.
