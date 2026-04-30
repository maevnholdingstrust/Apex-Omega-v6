# Flexible USDC Value Routing — Canonical Objective

## Objective

The routing engine starts with a USD-denominated asset, preferably USDCe/USDC on Polygon chain 137, moves through one or more intermediate tokens and executable venues, and must end with more USD-denominated value than it started with.

Canonical form:

```text
USDC_start
→ token_1 on venue_A
→ token_2 on venue_B
→ ...
→ USDC_end
```

A route is valid only if:

```text
USDC_end > USDC_start + all modeled costs
```

Modeled costs include DEX fees, AMM slippage, flashloan fee, gas, risk buffer, and mempool degradation.

---

## Discovery Logic

For every candidate route:

1. Start from a stable token: USDCe, USDC, USDT, or DAI.
2. Traverse available intermediate tokens.
3. Query all supported executable venues.
4. For each hop, compute the executable output, not just a static price.
5. Return to a USD-denominated stable token.
6. Rank only by final USD value after costs.

---

## Route Types

### Two-hop route

```text
USDC → Token A → USDC
```

### Three-hop route

```text
USDC → Token A → Token B → USDC
```

### N-hop route

```text
USDC → Token A → Token B → ... → USDC
```

N-hop routes are higher opportunity but higher risk because each hop adds fee drag, slippage, gas, calldata size, and failure probability.

---

## Strike Rule

A route may only produce an executable payload if:

```text
net_profit_usdc > MIN_NET_PROFIT_USD
```

Where:

```text
net_profit_usdc =
    final_usdc
  - initial_usdc
  - dex_fees
  - slippage_impact
  - flashloan_fee
  - gas_cost_usd
  - risk_buffer_usd
  - mempool_degradation_usd
```

---

## Safety Rules

1. Never treat raw spread as profit.
2. Never build calldata for unsupported venue adapters.
3. Never assume a token path is executable unless each hop has a valid pool/router adapter.
4. Never execute if route does not end in higher USDC value after full cost stack.
5. Always compare both directions.
6. Always prefer executable quote/output math over static display prices.

---

## Current Implementation Status

Implemented:

- USDC value route scanner for one intermediate token.
- Bidirectional venue comparison.
- Supported venue registry.
- Universal adapter framework.
- V2 and Uniswap V3 calldata support.

Next hardening:

- Apply full slippage model to each USDC-value route.
- Add N-hop graph search.
- Add Curve/Balancer/Algebra pool-specific adapters.
- Add final route-to-payload compiler for arbitrary supported paths.
