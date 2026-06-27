# SaaS Profit Split Targets

`ApexOmegaExecutionVMSplit.sol` is the alternative C1/C2 target for SaaS deployment.
`LiquidationExecutorSplit.sol` is the alternative liquidation target for SaaS deployment.

## Current Non-SaaS Targets

These are the currently verified active targets and should remain active until the SaaS split contracts are deployed, verified, router-authorized, and dry/fork proven.

```text
EXECUTOR_ADDRESS=0x35bb5D2212D709f60184CC5a0BaD54C213B608B0
C1_TARGET=0x35bb5D2212D709f60184CC5a0BaD54C213B608B0
C2_TARGET=0x35bb5D2212D709f60184CC5a0BaD54C213B608B0
LIQUIDATION_EXECUTOR_ADDRESS=0x67BB9f1bb8A90449C9071B673f62c18b9e8B451d
```

## Future SaaS Split Targets

These fields are intentionally separate from the active non-SaaS targets:

```text
EXECUTOR_ADDRESS_SAAS=0xEbe4fB2dc915fc0EAf3a4a32728014d6962aBF4F
C1_TARGET_SAAS=0xEbe4fB2dc915fc0EAf3a4a32728014d6962aBF4F
C2_TARGET_SAAS=0xEbe4fB2dc915fc0EAf3a4a32728014d6962aBF4F
LIQUIDATION_EXECUTOR_ADDRESS_SAAS=0x7DaF26AE7F08425D54CfDCc12d0FfD52B63734E4
SAAS_TREASURY_ADDRESS=0xaD3eF84259cFACB5D77a70911f85d39D2DBB49c6
```

## Verified SaaS Deployments

```text
ApexOmegaExecutionVMSplit
address: 0xEbe4fB2dc915fc0EAf3a4a32728014d6962aBF4F
deploy tx: 0xb49b91985aa3fe0151def2406211552652645503c99d7d8e0f14f2848618d7b9
block: 88286898
verification: Pass - Verified
router authorization tx: 0x612800f77f5f47d7051950dd59509118625f84154f4adfe7c0ef84e51de3eb41
authorized routers: 8

LiquidationExecutorSplit
address: 0x7DaF26AE7F08425D54CfDCc12d0FfD52B63734E4
deploy tx: 0x29e97d46310056a6a80f95ca8706f807fe575c0242663f8e6bfeb171e177af42
block: 88286908
verification: Pass - Verified
```

## Split Policy

- C1/C2 split VM: 30% of realized net profit goes to `owner()`.
- Liquidation split executor: 30% of liquidation profit goes to `profitReceiver`.
- Both split contracts send the remaining 70% to `platformTreasury`.
- BPS denominator is `10_000`.
- Rounding dust is assigned to `platformTreasury` because treasury receives `netProfit - ownerAmount`.

Formula:

```text
ownerAmount = netProfitRealized * 3_000 / 10_000
treasuryAmount = netProfitRealized - ownerAmount
```

## Current Owner Receiver

The VM owner and liquidation owner/profit receiver should remain:

```text
0xaD3eF84259cFACB5D77a70911f85d39D2DBB49c6
```

That wallet receives the 30% share and retains admin authority.

## 70% Distribution Options

The 70% should go first to a controlled treasury address, not directly to many SaaS parties from the executor. Keep executor settlement simple and deterministic.

Recommended production layout:

```text
C1/C2 VM Split or Liquidation Split
  30% -> owner wallet
  70% -> SaaS treasury / payment splitter / multisig
```

From the 70% treasury, distribute using a separate accounting layer:

- platform operations
- infrastructure/RPC/relay costs
- investor or partner revenue share
- user rebate or strategy-provider share
- tax/accounting reserve

This avoids adding complex multi-party accounting into flashloan execution.

## C1/C2 Split Deploy Command Shape

```bash
python python/deploy_contracts.py \
  --contract apex_vm_saas \
  --solc-version 0.8.24 \
  --platform-treasury 0xYOUR_TREASURY_ADDRESS \
  --verify
```

After deployment, update:

```text
EXECUTOR_ADDRESS_SAAS=<new split VM>
C1_TARGET_SAAS=<new split VM>
C2_TARGET_SAAS=<new split VM>
```

Do not replace the current verified VM until the split VM is deployed, verified, routers are authorized, and dry/fork execution passes.

## Liquidation Split Deploy Command Shape

```bash
python python/deploy_contracts.py \
  --contract liquidation_saas \
  --solc-version 0.8.24 \
  --profit-receiver 0xaD3eF84259cFACB5D77a70911f85d39D2DBB49c6 \
  --platform-treasury 0xYOUR_TREASURY_ADDRESS \
  --verify
```

After deployment, update:

```text
LIQUIDATION_EXECUTOR_ADDRESS_SAAS=<new split liquidation executor>
```

Do not replace the current verified liquidation executor until the split executor is deployed, verified, and at least one liquidation fork simulation proves liquidation, collateral exit, flash repayment, and split payout.
