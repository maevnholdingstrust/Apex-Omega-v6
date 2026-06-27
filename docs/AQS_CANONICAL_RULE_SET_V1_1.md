# AQS Canonical Rule Set v1.1

This document is the human-readable mirror of `python/ssot_pipeline/aqs_canon.py`.
The Python module is the executable source for dashboard and ZIP-audit checks.

## Execution Model

- `AQS-001`: Opportunity is the parent object. C1 and C2 may only exist under an Opportunity.
- `AQS-002`: C1 and C2 are execution cycles, not legs.
- `AQS-003`: LEG1 and LEG2 are swap legs and exist only inside execution cycles.

Required shape:

```text
Opportunity
├── C1
│   ├── LEG1
│   └── LEG2
└── C2
    ├── LEG1
    └── LEG2
```

## C1 Rules

- `AQS-004`: C1 is atomic: discovery validation, capital allocation, LEG1, LEG2, debt repayment, settlement, profit realization, and state commitment.
- `AQS-005`: `C1_NET_PROFIT > C1_MIN_PROFIT_THRESHOLD`.
- `AQS-006`: C1 cannot be a loss-leader.

## C2 Rules

- `AQS-007`: C2 is independent and shares no execution state with C1.
- `AQS-008`: C2 requires a valid C1 confirmation hash.
- `AQS-009`: C2 expires when `CURRENT_BLOCK - C1_BLOCK > 5`.
- `AQS-010`: Active C2 decisions are `MIRROR`, `REVERSE`, and `DO_NOTHING`; terminal state is `EXPIRED`.
- `AQS-011`: C2 must be independently profitable when executed.
- `AQS-012`: C2 cannot subsidize C1.

## Executable Price And Liquidity Rules

- `AQS-013`: `LEG1_EXECUTABLE_PRICE < LEG2_EXECUTABLE_PRICE`.
- `AQS-014`: Liquidity Monitor must approve TVL, depth, route capacity, slippage, and capital safety before capital injection.
- `AQS-015`: Default `TVL_GATE_USD = 5000` unless an official config override is present.

## Accounting Rules

- `AQS-016`: `OPPORTUNITY_PROFIT = C1_PROFIT + C2_PROFIT_IF_EXECUTED`.
- `AQS-017`: C1, C2, and liquidation cycles execute only when independently accretive.
- `AQS-021`: Operating expenses are separate from execution-cycle PnL.
- `AQS-022`: Rejected opportunities are tracked separately and do not affect execution or opportunity PnL.

Note: Multiplying `C1_PROFIT * C2_PROFIT` is rejected because it produces squared-dollar units. Profit is additive across independent execution cycles.

## Hashing Rules

- `AQS-018`: C1 settlement emits opportunity ID, execution ID, route hash, tx hash, realized profit, C1 confirmation hash, C1 post-state hash, and C1 block number.
- `AQS-019`: Route hashes are deterministic over opportunity ID, execution type, flash asset, flash amount, token path, venue path, calldata hash, min-outs, deadline, and recipient.
- `AQS-020`: Opportunity IDs are deterministic over chain ID, token pair, venues, block number, and initial state hash. Timestamp-only IDs are forbidden.

## Audit Output Format

Every violation must return:

```text
Rule ID:
Rule Name:
Violation Location:
Evidence:
Severity:
Impact:
Required Remediation:
Schema Changes:
Code Changes:
Documentation Changes:
Deployment Changes:
```

## Compliance Scoring

- Critical violation: `-15`
- High violation: `-8`
- Medium violation: `-3`
- Low violation: `-1`

Score bands:

- `100`: Fully Canonical
- `90-99`: Minor Remediation
- `75-89`: Significant Remediation
- `50-74`: Major Compliance Failure
- `0-49`: Critical Architectural Failure

Certification is denied when any critical violation exists.
