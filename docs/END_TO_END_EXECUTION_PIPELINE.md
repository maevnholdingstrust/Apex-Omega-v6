# Apex-Omega End-To-End Execution Pipeline

This is the canonical pipeline from live market discovery through C1/C2 execution.

```mermaid
flowchart TD
    A[Live Polygon RPC / WSS Reads] --> B[Pool + Token Discovery]
    B --> C[Price Coverage Resolver]
    C --> D{Every token priced?}
    D -- No --> D1[Quarantine pool/token and log diagnostics]
    D -- Yes --> E[Route Graph Builder]

    E --> F[Protocol Quote Layer]
    F --> F1[V2 CPMM reserve math + router getAmountsOut]
    F --> F2[Uniswap V3 QuoterV2]
    F --> F3[QuickSwap Algebra QuoterV2]
    F --> F4[Curve get_dy / get_dy_underlying]
    F --> F5[Balancer weighted math + queryBatchSwap]

    F --> G[Chained Output Simulation]
    G --> H[Net Profit Equation]
    H --> H1[gross = final_out - flash_in]
    H --> H2[net = gross - flash_fee - gas - protocol_fees - risk_buffer]
    H --> I{net >= min owner profit?}
    I -- No --> I1[Reject: no executable edge]
    I -- Yes --> J[Execution Candidate]

    J --> K[Live Per-Leg Quote Refresh]
    K --> L[Guaranteed MinOut Chain]
    L --> M{final_out >= principal + flash fee + gas + owner profit?}
    M -- No --> M1[Reject before calldata]
    M -- Yes --> N[RouteStep Payload Builder]

    N --> N1[V2 router calldata]
    N --> N2[V3 router calldata]
    N --> N3[Algebra router calldata]
    N --> N4[Curve pool exchange calldata]
    N --> N5[Balancer vault swap calldata]
    N --> O[Apex VM ExecutionContext]

    O --> P{C1 or C2?}
    P -- C1 --> Q[executeC1]
    P -- C2 --> R[executeC2 with confirmed C1 internal id]

    Q --> S{Flash source}
    R --> S
    S -- Aave V3 --> S1[flashLoanSimple]
    S -- Balancer V2 --> S2[flashLoan]
    S -- Balancer V3 --> S3[unlock / sendTo / settle]

    S1 --> T[VM Callback Boundary]
    S2 --> T
    S3 --> T

    T --> U[Venue Authorization Gate]
    U --> V[Low-Level Protocol-Agnostic Calls]
    V --> W[Per-Step minAmountOut Check]
    W --> X[Repayment Check]
    X --> Y[Final Owner Profit Check]
    Y --> Z[Profit Transfer To Owner]
```

## C1 / C2 Rules

- C1 and C2 are separate transaction envelopes.
- C2 is only eligible after C1 is confirmed.
- C2 is locked to the C1-derived pair/lane by the off-chain orchestrator.
- C2 expires after five blocks from the C1 block.
- Discovery cycles continue while C2 waits for its decision.
- C2 must choose `MIRROR`, `REVERSE`, or `DO_NOTHING` from post-C1 market state.

## VM Alignment

`ApexOmegaExecutionVM` does not hardcode DEX math. Math belongs off-chain and in fork simulation.
The VM executes any protocol only if the route builder provides:

- `venue`
- `tokenIn`
- `tokenOut`
- `amountIn`
- `minAmountOut`
- `callValue`
- `payload`

The VM then enforces:

- owner-only entrypoints
- emergency stop
- nonce guard
- optional Merkle guard
- authorized router/pool target
- flashloan callback source validation
- per-step minimum output
- final repayment plus owner-profit floor

## Deployment Order

1. Compile `contracts/ApexOmegaExecutionVM.sol`.
2. Deploy with:
   - Aave V3 Pool
   - Balancer V2 Vault
   - Balancer V3 Vault
3. Verify source on-chain.
4. Update both `C1_TARGET` and `C2_TARGET` to the VM address.
5. Authorize known routers as registry type `0`.
6. Authorize route-specific pools such as Curve pools as registry type `1`.
7. Run fork simulation for the first live-profit route.
8. Only submit after fork simulation passes.
