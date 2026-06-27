# Mainnet Readiness Grade

Generated: 2026-06-10

## Executive Grade

**Grade: B+ for deployment readiness.**

The executor VM is deployed, verified, locally wired as both C1 and C2, and the
off-chain planner now emits VM calldata automatically when C1/C2 targets match.
The system is not graded A/live-autonomous until a real route passes live quote,
fork simulation, and private submission formatting.

## On-Chain State

- Chain: Polygon PoS mainnet, `chainId=137`
- Apex VM: `0x35bb5D2212D709f60184CC5a0BaD54C213B608B0`
- Deploy tx: `0x5771157dd180544c7b1e77fd49d5efda92664816c47ee41a194b85942e48dd0b`
- Deploy block: `88285041`
- Verification: `Pass - Verified`
- Owner: `0xaD3eF84259cFACB5D77a70911f85d39D2DBB49c6`
- Emergency stop: `false`
- Global nonce: `0`

Flash sources:

- Aave V3 Pool: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`
- Balancer V2 Vault: `0xBA12222222228d8Ba445958a75a0704d566BF2C8`
- Balancer V3 Vault: `0xbA1333333333a1BA1108E8412f11850A5C319bA9`

Authorization:

- Router/vault authorization tx:
  `0x5b9aa8627e12dadd1c9bb6011c514f53cded3156f544d5441d47b6b58fbf28d2`
- Authorized known router/vault targets: `8/8`
- Direct pool targets, especially Curve pools, still require per-pool
  authorization before execution.

## Off-Chain State

The following local targets point to the deployed VM:

- `.env`: `C1_TARGET`, `C2_TARGET`, `EXECUTOR_ADDRESS`
- `python/apex_omega_core/.env`: `C1_TARGET`, `C2_TARGET`,
  `C1_INSTITUTIONAL_EXECUTOR_ADDRESS`
- `python/apex_omega_core/core/contract_targets.py`: `C1_TARGET`, `C2_TARGET`

Execution planner:

- `ExecutionEngine.build_c1_plan()` auto-routes to `executeC1(...)` when C1 and
  C2 targets match.
- `ExecutionEngine.build_c2_plan()` auto-routes to `executeC2(...)` when C1 and
  C2 targets match.
- C2 requires a confirmed `c1_internal_id`.

Protocol support in the VM path:

- V2 router calldata
- Uniswap V3 router calldata
- QuickSwap Algebra router calldata
- Curve pool calldata, when the specific pool is authorized
- Balancer vault calldata
- Custom low-level calldata, when target authorization exists

## Verification Results

Latest local proof:

```text
python -m pytest python\apex_omega_core\tests\test_expanded_strategy_steps.py python\apex_omega_core\tests\test_protocol_quote_builder.py python\apex_omega_core\tests\test_execution_compiler.py python\apex_omega_core\tests\test_execution_engine_entrypoints.py -q

62 passed, 1 warning
```

Syntax proof:

```text
python -m py_compile python\deploy_contracts.py python\apex_omega_core\core\execution_compiler.py python\apex_omega_core\core\execution_engine.py python\apex_omega_core\core\expanded_strategy_steps.py python\apex_omega_core\core\protocol_quote_builder.py

PASS
```

Cache cleanup:

- Workspace `__pycache__` directories removed.
- Runtime deployment proofs retained.

## Current Mainnet Blockers

1. No live route has passed final live-profit proof since VM deployment.
   Last payload validation rejected all old CSV rows because final live quote was
   below repayment plus owner-profit threshold.

2. Fork simulation gate has not passed for a VM-bound executable route.
   Submission must remain blocked until a route passes:
   live quote -> final repayment/profit -> fork sim.

3. Curve direct pools are not globally authorized.
   Each Curve pool target must be authorized with registry type `1` before a
   route using that pool can execute.

4. Private submission lane still needs final production decision.
   The VM is deployed, but live transaction submission should remain gated until
   the first fork pass and chosen relay path are confirmed.

## Required Next Checkpoint

1. Run a fresh discovery scan against the deployed VM configuration.
2. Validate live per-leg quotes.
3. Build a VM C1 payload for the first route that clears final owner profit.
4. Fork simulate that exact VM C1 payload.
5. Authorize any missing direct pool targets discovered by the route.
6. Only after fork pass, format private submission.
