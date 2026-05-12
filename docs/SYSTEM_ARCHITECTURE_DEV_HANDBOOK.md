# Apex-Omega v6 System Architecture Developer Handbook

## 1. Canonical Objective

Apex-Omega is a Polygon chain-137 arbitrage execution system built around a two-contract, two-stage execution cycle:

1. **C1 / Aggressor** executes first against the current executable market state.
2. **State mutates** after C1 lands.
3. **C2 / Surgeon** recomputes the same opportunity universe from the new post-C1 state and only executes if residual EV remains positive.

C1 never reserves edge for C2. C2 never uses pre-C1 reserves as permission to strike.

---

## 2. End-to-End Pipeline

```text
Discovery
→ executable quote normalization
→ raw spread classification
→ deterministic sizing/math
→ opportunity validation
→ mempool degradation check
→ lane reservation
→ C1 execution plan
→ calldata compilation
→ signing
→ private bundle submission
→ C1 inclusion observation
→ post-C1 pool state refresh
→ C2 recomputation
→ C2 validation
→ C2 execution/idle decision
→ audit log
```

Detailed stage-by-stage symbol coverage lives in
[`docs/PIPELINE_STAGE_FUNCTION_VARIABLE_MATH_INDEX.md`](PIPELINE_STAGE_FUNCTION_VARIABLE_MATH_INDEX.md).

---

## 3. Core Modules

### `runtime_config.py`
Central environment loader and execution safety authority.

Expected capabilities:
- Load `.env` from repository root or package path.
- Normalize booleans, integers, and floats.
- Expose runtime thresholds and contract addresses.
- Block live sending unless `LIVE_TRADING_ENABLED=true` and `DRY_RUN=false`.

Tested expectations:
- Missing live vars produce fail-fast errors.
- Dry run blocks send paths.
- Relays are collected from configured env variables.

### `execution_compiler.py`
Existing ABI compiler.

Expected capabilities:
- Compile InstitutionalExecutor route envelopes for C1.
- Compile UltimateArbitrageExecutor route envelopes for C2.
- Preserve min-profit and profit asset metadata.

### `execution_engine.py`
Runtime bridge from strategy output to signed/bundled execution.

Expected capabilities:
- Build C1 execution plans from strategy output.
- Build C2 execution plans from post-C1 strategy output.
- Validate profit/slippage/TVL gates.
- Sign transactions using executor key.
- Submit bundles through relay submitter.

### `relay_submitter.py`
Private bundle submission layer.

Expected capabilities:
- Build `eth_sendBundle` JSON-RPC payloads.
- Submit raw signed transactions to all configured relays.
- Return structured per-relay status.
- Refuse submission if runtime config is not live-safe.

### `lane_manager.py`
Nonce/lane allocator for multi-lane execution.

Expected capabilities:
- Maintain 32 execution lanes by default.
- Reserve lanes for opportunities.
- Assign deterministic pending nonces.
- Track submitted tx hashes.
- Release lanes after terminal state.

### `mempool_simulator.py`
Pre-execution degradation layer.

Expected capabilities:
- Apply conservative pending-state degradation.
- Reject if output degradation exceeds configured threshold.
- Provide a drop-in interface for future decoded pending-tx simulation.

### `c2_trigger_system.py`
Post-C1 observer and C2 decision orchestrator.

Expected capabilities:
- Observe C1 receipt.
- Require successful C1 inclusion before C2 evaluation.
- Refresh post-C1 state through an injected callback.
- Build a C2 strategy output through an injected callback.
- Validate C2 opportunity gates.
- Return IDLE when residual EV is not positive or validation fails.

---

## 4. Canonical Data Contracts

### Strategy Output

```python
{
    "asset": "0x...",
    "min_profit": 1,
    "steps": [...],
    "gas_reserve_asset": 0,
    "dex_fee_reserve_asset": 0,
}
```

### Opportunity Object

```python
{
    "opportunity_id": "...",
    "net_profit_usd": 10.0,
    "slippage_bps": 20.0,
    "pool_tvl_usd": 100000.0,
    "expected_output": 1000.0,
}
```

### Execution Plan

```python
ExecutionPlan(
    target="institutional" | "ultimate",
    compiled=CompiledExecution(...),
    calldata=b"...",
)
```

---

## 5. Validation Gates

A trade must pass all gates:

1. RPC reachable.
2. Config live-safe only when sending.
3. Net profit above `MIN_NET_PROFIT_USD`.
4. Route slippage below `MAX_ROUTE_SLIPPAGE_BPS`.
5. Pool TVL above `MIN_POOL_TVL_USD`.
6. Mempool degradation below `MAX_MEMPOOL_DEGRADATION_BPS`.
7. Lane nonce reserved.
8. Calldata non-empty.
9. Bundle target block valid.
10. Relay submission attempted only in live-safe mode.

---

## 6. C1/C2 Rules

### C1 Aggressor
- Uses current live executable state.
- Executes first.
- Mutates pool state.
- Is complete even if C2 later idles.

### C2 Surgeon
- Waits for C1 inclusion.
- Re-fetches post-C1 state.
- Recomputes opportunity from new state.
- May mirror, reverse, or idle.
- Must idle if residual EV is not positive.

---

## 7. Safe Operating Modes

### Dry Run
```env
LIVE_TRADING_ENABLED=false
DRY_RUN=true
```
Allowed:
- Build plans.
- Compile calldata.
- Build bundle payloads.
- Simulate gates.

Blocked:
- Signing/sending live transactions.
- Relay bundle submission.

### Live Mode
```env
LIVE_TRADING_ENABLED=true
DRY_RUN=false
```
Required:
- `POLYGON_RPC`
- `EXECUTOR_PRIVATE_KEY`
- `C1_INSTITUTIONAL_EXECUTOR_ADDRESS`
- `C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS`
- `AAVE_V3_POOL_ADDRESS`

---

## 8. Expected Test Coverage

Minimum required tests:

- Runtime config parsing.
- Runtime send safety refusal.
- C1 plan builds from valid strategy output.
- C2 plan builds from valid strategy output.
- Empty relay bundle rejects.
- Mempool degradation rejects unsafe output.
- Lane manager reserves/releases lanes.
- C2 trigger idles on failed C1 receipt.
- C2 trigger idles on failed post-C1 EV.
- C2 trigger returns plan when post-C1 opportunity passes.

---

## 9. Production Readiness Checklist

Before live funds:

- [ ] CI green.
- [ ] `.env` populated locally or via secrets manager.
- [ ] C1 executor deployed and verified.
- [ ] C2 executor deployed and verified.
- [ ] RPC endpoints pass chain-id and block freshness checks.
- [ ] Relays respond to health probes.
- [ ] Fork simulation passes full C1 → post-state → C2 cycle.
- [ ] Nonce lane snapshot clean.
- [ ] Kill switch tested.
- [ ] Dashboard/logging captures terminal states.

---

## 10. Known Limits / Next Hardening

Current mempool simulator is conservative but not full decoded pending-tx replay. The next hardening step is decoding pending router calls, projecting reserve deltas per touched pool, and recomputing exact post-pending outputs before bundle submission.
