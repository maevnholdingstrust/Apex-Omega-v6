# PDF Alignment Findings

Generated: 2026-06-12

Source documents extracted into:

```text
runtime/doc_alignment/
```

## Primary Alignment Rule

The PDFs define a mandatory route-level executable price invariant:

```text
BUY_LEG1_PRICE < SELL_LEG2_PRICE
```

This rule must be computed from executable quote amounts, not spot prices or hardcoded assumptions.

## Current Alignment Findings

### Aligned

- C1 and C2 are separate atomic flashloan executions.
- C2 references C1 state and is constrained to `block > C1 block` and `block <= C1 block + 5`.
- C2 expiry is lane-local and does not stop global scanning.
- The Apex VM verifies:
  - flashloan receipt
  - venue authorization
  - per-hop `minAmountOut`
  - final repayment plus `minNetProfit`
  - profit transfer to owner
- The scanner uses live protocol quotes for V2/V3/Algebra/Curve/Balancer-capable paths.
- Broadcast remains disabled unless explicitly enabled.

### Previously Misaligned

- Route artifacts did not explicitly expose:
  - `buy_leg1_price`
  - `sell_leg2_price`
  - `leg_price_executable_spread_abs`
  - `leg_price_executable_spread_bps`
  - `leg_price_invariant_status`
  - `leg_price_invariant_reason`
  - `leg_price_quote_block`
- Payload gating did not independently reject stale/external rows missing the LEG1/LEG2 invariant proof.
- Dashboard/CSV output could show net profit without exposing the mandatory executable price invariant behind the route.

## Corrections Applied

### Scanner Record Schema

Added to `OpportunityRecord` in `python/dry_run.py`:

```text
buy_leg1_price
sell_leg2_price
leg_price_executable_spread_abs
leg_price_executable_spread_bps
leg_price_invariant_status
leg_price_invariant_reason
leg_price_quote_block
```

### Invariant Function

Added `_leg_price_invariant(...)` in `python/dry_run.py`.

For any route with two or more hops:

```text
LEG1_AMOUNT_IN_A  = leg_amounts_in[0]
LEG1_AMOUNT_OUT_B = leg_amounts_out[0]
LEG2_AMOUNT_IN_B  = leg_amounts_in[1]
LEG2_AMOUNT_OUT_A = leg_amounts_out[-1]

BUY_LEG1_PRICE  = LEG1_AMOUNT_IN_A / LEG1_AMOUNT_OUT_B
SELL_LEG2_PRICE = LEG2_AMOUNT_OUT_A / LEG2_AMOUNT_IN_B
```

Pass condition:

```text
BUY_LEG1_PRICE < SELL_LEG2_PRICE
```

Fail reasons include:

```text
LEG_PRICE_AMOUNTS_MISSING
LEG1_AMOUNT_IN_A_INVALID
LEG1_AMOUNT_OUT_B_INVALID
LEG2_AMOUNT_IN_B_INVALID
LEG2_AMOUNT_OUT_A_INVALID
BUY_LEG1_PRICE_NOT_BELOW_SELL_LEG2_PRICE
```

### Producer Gates

The invariant is now enforced before producing executable opportunity records for:

- two-leg pair arbitrage
- triangular route path
- expanded graph candidates
- live quoted 2-4 hop cycles

### Payload Gate

`scripts/autonomous_live_scanner.py` now rejects payload building when:

- invariant fields are missing
- invariant status is not `LEG_PRICE_EDGE_VALID`

This prevents stale CSV rows or external rows from becoming C1 payload candidates without the mandatory price proof.

## Proof

Focused tests:

```text
pytest python\apex_omega_core\tests\test_leg_price_invariant.py python\apex_omega_core\tests\test_expanded_strategy_steps.py python\apex_omega_core\tests\test_v2_cpmm_math.py -q

15 passed, 1 warning
```

Syntax checks:

```text
python -m py_compile python\dry_run.py scripts\autonomous_live_scanner.py
```

Result:

```text
passed
```

Fresh no-broadcast scan:

```text
python scripts\autonomous_live_scanner.py --once
```

Result:

```text
RPC: https://rpc.flashbots.net/polygon
Chain: 137
Mode: dry_run_no_broadcast
Tokens priced: 15/15
Cycles evaluated: 234
Profitable records: 0
Payload candidates: 0
```

CSV proof:

```text
dry_run_results.csv
```

Now includes:

```text
buy_leg1_price
sell_leg2_price
leg_price_executable_spread_abs
leg_price_executable_spread_bps
leg_price_invariant_status
leg_price_invariant_reason
leg_price_quote_block
```

## Remaining Misalignment

- The dashboard should surface the new invariant columns directly in the route table.
- The execution ledger should persist C1/C2 rows with invariant proof, payload proof, fork proof, and final realized state.
- Curve remains quote-capable but not fork-proven; prior proof failed at `ERR_VENUE_PAYLOAD_REVERTED`.
- Balancer and Algebra are adapter-capable but still need successful market-route fork proof.

## Current Verdict

```text
Architecture alignment: improved
Mandatory LEG1/LEG2 invariant: enforced in scanner + payload gate
Live broadcast readiness: no
Reason: no fresh route passed live quote profitability and fork proof
```
