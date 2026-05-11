# Flashloan Sizing Rule — Step-1 TVL Cap

## Canonical Rule

Every live execution route begins with a USD-denominated flashloan request, preferably USDCe/USDC on Polygon chain 137.

The flashloan amount must be capped by the first executable pool touched by the route:

```text
flashloan_amount_usdc <= 0.15 * step1_pool_tvl_usd
```

This is a hard safety ceiling, not automatically the final trade size.

---

## Why Step 1 Matters

Step 1 is the first point where borrowed capital touches live liquidity. If the first swap is oversized, it can deform the pool price enough to destroy the route before later legs execute.

The Step-1 TVL cap prevents a route from borrowing more USDC than the opening pool can reasonably absorb.

---

## Correct Production Sizing

Final execution size must be the smallest safe value across all sizing constraints:

```text
final_flashloan_amount_usdc = min(
    0.15 * step1_pool_tvl_usd,
    reserve_based_optimal_input_usdc,
    max_slippage_size_usdc,
    downstream_route_depth_limit_usdc,
    available_flashloan_liquidity_usdc,
    system_risk_cap_usdc
)
```

So the 15% Step-1 TVL rule is a maximum cap. The AMM/reserve optimizer may reduce the actual trade size.

---

## Benefits

1. Prevents first-leg market impact from destroying route profitability.
2. Reduces false positives caused by static spread calculations.
3. Makes route size proportional to real liquidity.
4. Protects thin pools from oversized execution.
5. Creates a deterministic safety limit for dry-run and live execution.

---

## Strike Rule Alignment

A route may only strike if:

```text
USDC_end > USDC_start + all modeled costs
```

Where modeled costs include DEX fees, AMM slippage, gas, flashloan fees, risk buffer, and mempool degradation.

The Step-1 TVL cap controls size. The USDC value rule controls profitability.
