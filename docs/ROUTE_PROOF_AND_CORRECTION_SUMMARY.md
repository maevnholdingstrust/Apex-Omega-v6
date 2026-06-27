# Apex-Omega v6 Route Proof And Correction Summary

Generated: 2026-06-12

## Safety Mode

The current runtime is configured for proof-only operation:

- `DRY_RUN=true`
- `LIVE_TRADING_ENABLED=false`
- `BROADCAST_ENABLED=false`
- `REQUIRE_FORK_SIM=true`
- Chain: Polygon PoS mainnet, `chain_id=137`

No command in this proof pass signed or broadcast a transaction.

## Corrections Completed

### Runtime RPC

Problem:

- The autonomous scanner was failing because `https://polygon.drpc.org` returned `429 Too Many Requests`.
- `logs/autonomous_scanner_status.json` previously reported:
  - `status=error`
  - `Cannot reach Polygon RPC after 3 attempts`

Correction:

- `.env` runtime read/simulation RPC was moved to the reachable Polygon endpoint:
  - `POLYGON_RPC=https://rpc.flashbots.net/polygon`
  - `POLYGON_HTTP=https://rpc.flashbots.net/polygon`
  - `POLYGON_RPC_URL=https://rpc.flashbots.net/polygon`
  - `PRIVATE_RPC_URL=https://rpc.flashbots.net/polygon`

Proof:

- Endpoint probe returned `eth_blockNumber` successfully for `https://rpc.flashbots.net/polygon`.
- Fresh autonomous scan connected:
  - `Connected. Block #25305463`
  - `chain_id=137`

### RuntimeConfig Test Fixture Drift

Problem:

- `python/apex_omega_core/tests/test_expanded_strategy_steps.py` constructed `RuntimeConfig` without the newer flashloan sizing fields.
- Test failure:
  - missing `min_flash_loan_usd`
  - missing `max_flash_loan_usd`
  - missing `autonomous_max_flashloan_cap_usd`

Correction:

- Added the required fields to the test fixture:
  - `min_flash_loan_usd=1000.0`
  - `max_flash_loan_usd=100000.0`
  - `autonomous_max_flashloan_cap_usd=100000.0`

Proof:

```text
pytest python\apex_omega_core\tests\test_expanded_strategy_steps.py python\apex_omega_core\tests\test_v2_cpmm_math.py python\apex_omega_core\tests\test_route_graph.py -q

66 passed, 1 warning
```

### Syntax / Import Proof

Proof:

```text
python -m py_compile scripts\prove_best_route_fork.py scripts\autonomous_live_scanner.py python\dry_run.py python\apex_omega_core\core\expanded_strategy_steps.py python\apex_omega_core\core\swap_adapters.py python\apex_omega_core\core\execution_compiler.py python\apex_omega_core\core\execution_engine.py
```

Result:

- Passed with no syntax errors.

## Current Scanner Proof

Command:

```text
python scripts\autonomous_live_scanner.py --once
```

Result:

- RPC: `https://rpc.flashbots.net/polygon`
- Mode: `dry_run_no_broadcast`
- Autonomous mode: `continuous_until_stopped`
- Scan cycle: `1`
- Elapsed: `221.827s`
- Chain: `137`
- Flashloan provider: `balancer`
- Min net profit: `$5.00`
- Min pool TVL: `$5,000`
- Max flashloan cap: `$100,000`
- Sizing policy: `opportunity_driven_optimal_flashloan_sizing`
- Target records: `unbounded`
- Scan rounds for this proof run: `1`

Discovery counters:

- Raw pairs: `27`
- Raw pools: `39`
- Graph edges: `19`
- Pair-arb pairs: `4`
- Tokens priced: `15/15`
- Quarantined pools: `22`
- Cycles evaluated: `234`
- Complete live quote paths: `0`
- Profitable records: `0`
- Payload candidates: `0`
- Payload validated: `0`

Conclusion:

- The scanner is functional in dry mode.
- Price coverage was complete for the discovered token set.
- This scan did not produce a profitable route, so there was no new payload/fork candidate to prove.

## Latest Proof Boundary

The latest fresh scan stopped at:

```text
proof_checkpoint = blocked_at_live_quote_or_payload_validation
```

Reason:

- No route passed live chained quote profitability in the fresh cycle.
- Therefore no C1 payload was built from that cycle.
- Therefore no new `eth_call` fork proof was possible from that cycle.

This is correct fail-closed behavior.

## Prior Route Proof Evidence

Existing artifact:

```text
runtime/fork_route_proof.json
```

Route:

```text
USDCe -> USDC -> USDT -> USDCe
univ3_100 -> univ3_100 -> curve_ss
```

Payload:

- Target: `0x35bb5D2212D709f60184CC5a0BaD54C213B608B0`
- Target bytecode exists: `true`
- Target bytecode bytes: `12025`
- C1 payload was built.
- Live per-leg quote diagnostics were present.

Fork/eth_call result:

```text
ok=false
error=ERR_VENUE_PAYLOAD_REVERTED
```

Conclusion:

- The route reached payload construction.
- The target contract exists.
- The final proof failed inside a venue call.
- The likely failing family is the Curve leg, because the route was `UniV3 -> UniV3 -> Curve`.
- This route is not executable until the Curve exchange calldata path is corrected and passes `eth_call`.

## Adapter Capability Matrix

| Adapter family | Live quote support | Payload build support | Fresh profitable route found | Fork proof status |
| --- | --- | --- | --- | --- |
| V2 CPMM | Yes | Yes | No | Not proven this cycle |
| Uniswap V3 | Yes, via Quoter V2 | Yes | No | Not proven this cycle |
| QuickSwap Algebra | Adapter present | Yes | No | Not proven this cycle |
| Curve StableSwap | Yes, `get_dy` / `get_dy_underlying` | Yes | Previously found | Failing at venue payload |
| Balancer Weighted | Yes, `queryBatchSwap` | Yes | No | Not proven this cycle |

## What Is Proven

Proven:

- Runtime now uses a reachable Polygon RPC.
- Scanner connects to Polygon mainnet chain `137`.
- Scanner runs in no-broadcast dry mode.
- Price coverage completed for the latest discovered token set.
- Opportunity sizing is configured as opportunity-driven, not fixed-size.
- Expanded adapter tests pass.
- V2 CPMM math tests pass.
- Route graph tests pass.
- Syntax/import checks pass for the active proof modules.

Not proven:

- A fresh profitable route.
- A fresh C1 payload from the latest scan.
- A successful fork/eth_call execution for every adapter family.
- Curve venue execution through the Apex VM.
- Balancer weighted venue execution through the Apex VM.
- Algebra execution through the Apex VM.

## Remaining Blockers

### 1. No Fresh Profitable Route

The latest scan produced zero profitable records. That means payload/fork proof cannot advance from the latest market state.

Next action:

- Keep the autonomous scanner running across more cycles using the reachable RPC.
- Do not reduce execution gates just to force fake positives.

### 2. Curve Payload Revert

The prior best route built a payload but failed `eth_call` with:

```text
ERR_VENUE_PAYLOAD_REVERTED
```

Next action:

- Isolate the Curve step.
- Verify exact Polygon Curve pool function support:
  - `exchange(int128,int128,uint256,uint256)`
  - `exchange_underlying(int128,int128,uint256,uint256)`
  - any pool-specific overload requiring receiver.
- Confirm coin index mapping for `USDT -> USDCe`.
- Rebuild the Curve step with the exact live-quoted function selector.
- Re-run `eth_call`.

### 3. Balancer And Algebra Need Live Route Proofs

The code supports Balancer and Algebra quote/payload construction, but the latest scan did not find a profitable route that uses them.

Next action:

- Continue scanning until a route containing those adapters passes live quote profitability.
- Then run payload build and `eth_call`.

## Required Route Proof Standard

Each route variation is only considered proven when all five gates pass:

1. Discovery finds the route.
2. Every leg has a live protocol quote.
3. Final output exceeds flashloan repayment, flash fee, gas, and owner profit threshold.
4. C1 payload builds with the flashloan receiver set to the configured executor.
5. No-broadcast `eth_call` succeeds against Polygon mainnet state or a fork at the selected block.

Anything less is adapter-capable, not execution-proven.

## Current Verdict

System status:

```text
Discovery pipeline: WORKING
Price coverage: WORKING
Adaptive sizing: CONFIGURED
Payload builder: PARTIALLY PROVEN BY TESTS
Fork proof: BLOCKED UNTIL PROFITABLE ROUTE OR CURVE FIX
Live execution: NOT READY
Broadcast: DISABLED
```

Production readiness:

```text
PARTIAL
```

The system is safer than before because it fails closed. It is not yet end-to-end execution ready because no fresh route passed all proof gates, and the prior Curve-containing route still fails venue execution.
