# Protocol Interface Checkpoint — 2026-06-27

Branch: `apex-live-payload-hardening`
PR: #63

## Purpose

Freeze the current execution-interface checkpoint and document the audited protocol assumptions before any additional live routing code is merged.

## Scope audited

- Uniswap V2-style routers
- Uniswap V3-style SwapRouter / Quoter interfaces
- Algebra / QuickSwap V3-style concentrated liquidity interfaces
- Balancer V3 Vault transient accounting
- Internal VM payload and invariant interfaces

## Current branch checkpoint

### Added execution layers

- `python/apex_omega_core/core/executable_payloads.py`
- `python/apex_omega_core/core/c1_payload_gate.py`
- `python/apex_omega_core/core/execution_vm_calldata.py`
- `python/apex_omega_core/core/dex_execution_adapters.py`
- `python/apex_omega_core/core/executable_opportunity_builder.py`
- `python/apex_omega_core/core/live_dex_queries.py`
- `python/apex_omega_core/core/balancer_v3_vault_adapter.py`

### Added tests

- `python/apex_omega_core/tests/test_executable_payloads.py`
- `python/apex_omega_core/tests/test_contract_invoker_vm_payload.py`
- `python/apex_omega_core/tests/test_dex_execution_adapters.py`
- `python/apex_omega_core/tests/test_executable_opportunity_builder.py`
- `python/apex_omega_core/tests/test_live_dex_queries.py`
- `python/apex_omega_core/tests/test_balancer_v3_vault_adapter.py`

## Protocol interface audit

### V2 router interface

Status: accepted.

Execution interface:

```solidity
swapExactTokensForTokens(uint256 amountIn,uint256 amountOutMin,address[] calldata path,address to,uint256 deadline)
```

Quote interface:

```solidity
getAmountsOut(uint256 amountIn,address[] calldata path)
```

Current implementation:

- `live_dex_queries.py` calls `getAmountsOut(uint256,address[])`.
- `dex_execution_adapters.py` encodes `swapExactTokensForTokens(uint256,uint256,address[],address,uint256)`.
- No dashboard mid-price is accepted as an executable quote.
- Quote output must be positive.
- `quoteAmountOut >= minAmountOut` is enforced.

### V3 / concentrated liquidity router interface

Status: accepted for Uniswap V3-style routers.

Execution interface:

```solidity
exactInputSingle((address tokenIn,address tokenOut,uint24 fee,address recipient,uint256 deadline,uint256 amountIn,uint256 amountOutMinimum,uint160 sqrtPriceLimitX96))
```

Quote interfaces:

```solidity
quoteExactInputSingle(address tokenIn,address tokenOut,uint24 fee,uint256 amountIn,uint160 sqrtPriceLimitX96)
quoteExactInputSingle((address tokenIn,address tokenOut,uint256 amountIn,uint24 fee,uint160 sqrtPriceLimitX96))
```

Current implementation:

- `live_dex_queries.py` supports V3 Quoter V1 and V3 Quoter V2 decoding.
- `dex_execution_adapters.py` encodes SwapRouter `exactInputSingle` tuple calldata.
- Fee must be positive.
- `sqrtPriceLimitX96` must be non-negative.
- No execution quote is emitted if the quoter call fails or returns non-positive output.

### Algebra / QuickSwap V3 compatibility

Status: partially compatible, must be verified per deployed router/quoter ABI before enabling venue execution.

Notes:

- Algebra-style routers may use Uniswap V3-like exact-input semantics, but ABI and quoter return shapes can differ by deployment/version.
- Current adapter should be classified as `V3_COMPATIBLE` only after router and quoter ABI checks pass for the target Polygon venue.
- Do not auto-route Algebra venues through the Uniswap V3 encoder without an ABI allowlist.

Required before live Algebra enablement:

- Router ABI allowlist.
- Quoter ABI allowlist.
- Fee/tick-spacing mapping validation.
- Fork simulation match.

### Balancer V3 Vault

Status: adapter added; callback execution must be integrated with the on-chain executor before live enablement.

Current Vault model:

```solidity
unlock(bytes calldata data) external returns (bytes memory result)
settle(IERC20 token,uint256 amountHint) external returns (uint256 credit)
sendTo(IERC20 token,address to,uint256 amount) external
swap(VaultSwapParams memory vaultSwapParams) external returns (uint256 amountCalculatedRaw,uint256 amountInRaw,uint256 amountOutRaw)
```

Current implementation:

- `balancer_v3_vault_adapter.py` encodes `unlock(bytes)`.
- `balancer_v3_vault_adapter.py` encodes `settle(address,uint256)`.
- `balancer_v3_vault_adapter.py` encodes `sendTo(address,address,uint256)`.
- `balancer_v3_vault_adapter.py` encodes `swap((uint8,address,address,address,uint256,uint256,bytes))`.
- The adapter fails closed unless every token touched by the declared swap plan has a settlement hint.

Important limitation:

- The adapter builds calldata and validates settlement hints, but the actual Balancer V3 Vault execution still requires the receiving executor contract to implement the callback logic invoked by `unlock(bytes)`.
- Off-chain validation cannot prove zero non-zero deltas after callback. Fork simulation remains mandatory.

Required before live Balancer V3 enablement:

- Executor callback ABI lock.
- Callback payload schema lock.
- Fork sim verifying no residual Vault deltas.
- `getNonzeroDeltaCount()` and/or token delta checks where available.

## Internal invariant interface audit

### Required route invariants

- First token in route must equal flashloan asset.
- Final token out must equal flashloan asset.
- Profit asset must equal flashloan asset for same-asset round trip.
- Adjacent steps must be continuous: previous `tokenOut == next tokenIn`.
- `leg1BuyPrice < leg2SellPrice`.
- `netProfitUsd = grossProfitUsd - flashFeeUsd - gasCostUsd - riskBufferUsd`.
- Candidate must be `EXECUTABLE_PROFIT_CANDIDATE`.
- Lock must be `LOCKED_FOR_EXECUTION` before VM calldata is returned.

### Required execution gates

- No quote fallback.
- No synthetic output.
- No dashboard mid-price execution classification.
- No Balancer V3 Vault call without unlock/settle plan.
- No Algebra execution without ABI allowlist.
- Fork simulation required before live send.

## Checkpoint conclusion

The branch now contains the V2/V3 query layer, V2/V3 calldata adapter, Balancer V3 unlock/settle adapter, C1 VM payload schema, C1 gate, and VM calldata encoder.

This checkpoint does not declare the branch merge-ready. Python CI must be green before merge. Balancer V3 and Algebra venue activation must remain disabled until callback/ABI allowlists and fork simulation checks are complete.
