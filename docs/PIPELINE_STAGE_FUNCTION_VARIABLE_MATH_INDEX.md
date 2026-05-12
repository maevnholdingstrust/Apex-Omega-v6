# Pipeline Stage Function / Variable / Math Index

This index maps the end-to-end pipeline in `docs/SYSTEM_ARCHITECTURE_DEV_HANDBOOK.md` to the concrete Python modules, public functions, key variables, and math used by each stage.

The canonical stage order is:

`Discovery -> executable quote normalization -> raw spread classification -> deterministic sizing/math -> opportunity validation -> mempool degradation check -> lane reservation -> C1 execution plan -> calldata compilation -> signing -> private bundle submission -> C1 inclusion observation -> post-C1 pool state refresh -> C2 recomputation -> C2 validation -> C2 execution/idle decision -> audit log`

---

## 1. Discovery

**Primary files**
- `python/apex_omega_core/core/multi_market_scanner.py`
- `python/apex_omega_core/core/polygon_market_registry.py`

**Function index**
- `_fetch_v2_quote()` - discovers V2 pools and reserve-backed quotes.
- `_fetch_v3_quote()` - discovers V3 pools and selects the best liquidity tier.
- `quotes_for_pair()` - aggregates venue quotes for one pair.
- `scan_multi_market()` - emits `ScannerOpportunity` candidates.
- `scan_usdc_value_routes()` - emits `UsdcValueRoute` paths in USD terms.

**Variable / data index**
- `V2_FACTORY_ABI`, `V2_PAIR_ABI`, `V3_FACTORY_ABI`, `V3_POOL_ABI`
- `V3_FEES`, `STABLES`, `ZERO`
- `MarketQuote.{venue,pool,kind,fee_bps,price_quote_per_base,liquidity_hint}`
- `ScannerOpportunity.{buy_venue,sell_venue,buy_price,sell_price,raw_spread_bps}`
- `UsdcValueRoute.{start_amount_usdc,mid_amount,final_amount_usdc,gross_profit_usdc,estimated_cost_usdc,net_profit_usdc}`

**Math index**
- V2 human reserves: `h0 = r0 / 10^dec0`, `h1 = r1 / 10^dec1`
- V2 price: `price = h1 / h0` or `h0 / h1`
- V3 price: `price = (sqrtPriceX96 / 2^96)^2 * 10^(dec0 - dec1)`
- Spread basis points: `((sell_price - buy_price) / buy_price) * 10_000`
- USD route net: `gross - (flash_fee + risk_buffer + mempool_degradation)`

---

## 2. Executable quote normalization

**Primary files**
- `python/apex_omega_core/core/pool_math_registry.py`
- `python/apex_omega_core/core/spread_alignment.py`
- `python/apex_omega_core/core/slippage_sentinel.py`

**Function index**
- `normalize_address()` - canonicalizes factory/pool addresses.
- `get_pool_math_profile()` - maps venue/factory to execution math profile.
- `classify_pool_kwargs()` - emits a normalized `ClassifiedPool`.
- `align_spread()` - validates and preserves a well-formed `Spread`.
- `SlippageSentinel.classify_pool_family()` - maps route legs into executable math families.
- `SlippageSentinel.assert_family_supported_for_execution()` - blocks unsupported quote paths.

**Variable / data index**
- `PoolFamily`, `MathMode`
- `PoolMathProfile.{pool_family,math_mode,router_type,quote_engine,calldata_engine,execution_supported}`
- `ClassifiedPool.{reserve0,reserve1,sqrt_price_x96,tick,liquidity,fee_bps,fee_tier}`
- `Spread.{symbol,bid,ask,timestamp}`
- `FamilyQuote.{amount_out,family,backend}`

**Math index**
- No transformation is applied by `align_spread()`; it is a validation boundary.
- Normalization selects the correct math family before execution:
  - `RESERVE_CPMM`
  - `TICK_CLMM`
  - `CURVE_STABLESWAP`
  - `BALANCER_WEIGHTED`

---

## 3. Raw spread classification

**Primary files**
- `python/apex_omega_core/core/spread_alignment.py`
- `python/apex_omega_core/core/slippage_sentinel.py`

**Function index**
- `compute_raw_spread()`
- `compute_raw_spread_bps()`
- `SlippageSentinel.compute_raw_spread()`

**Variable / data index**
- `best_buy_price`
- `best_sell_price`
- `raw_spread`
- `raw_spread_bps`

**Math index**
- `raw_spread = best_sell_price - best_buy_price`
- `raw_spread_bps = ((best_sell_price - best_buy_price) / best_buy_price) * 10_000`

---

## 4. Deterministic sizing / math

**Primary files**
- `python/apex_omega_core/core/slippage_sentinel.py`
- `python/apex_omega_core/core/deterministic_slippage.py`
- `python/apex_omega_core/core/v2_cpmm_math.py`
- `python/apex_omega_core/core/live_strategy_steps.py`

**Function index**
- `amount_out_cpmm()`
- `SlippageSentinel.amm_swap()`
- `SlippageSentinel.two_leg_arb_profit()`
- `SlippageSentinel.optimal_two_leg_input()`
- `SlippageSentinel.optimize()`
- `calculate_deterministic_slippage_bps()`
- `calculate_cpmm_output_slippage_bps()`
- `max_leg_slippage_bps()`
- `build_live_strategy_output_from_state()`

**Variable / data index**
- `a_in`, `b_out_1`, `a_out_2`
- `r1_in`, `r1_out`, `r2_in`, `r2_out`
- `fee1`, `fee2`, `fee_bps`, `flash_fee_bps`
- `c_gas`, `c_loan`, `c_other`
- `p_gross`, `p_net`, `owner_submission_edge`
- `amount_in`, `amount_in_raw`, `leg1_out_raw_min`, `leg2_out_raw_min`
- `optimal_input`, `final_output`, `raw_profit`, `total_cost_usd`, `net_profit_usd`

**Math index**
- CPMM output: `out = x_eff * R_out / (R_in + x_eff)`, where `x_eff = amount_in * (1 - fee)`
- Two-leg gross profit: `p_gross = a_out_2 - a_in`
- Two-leg net profit: `p_net = p_gross - loan_cost - c_other`
- Owner submission edge: `owner_submission_edge = p_net - c_gas`
- Optimal two-leg input:
  - `g1 = 1 - fee1`
  - `g2 = 1 - fee2`
  - `num = sqrt(g1*g2*r1_in*r1_out*r2_in*r2_out) - r1_in*r2_in`
  - `denom = g1 * (r2_in + g2*r1_out)`
  - `a_in* = num / denom`
- Deterministic CPMM impact: `impact = 1 - reserve / (reserve + effective_size)`
- Slippage basis points: `impact * 10_000`
- Min-out buffer: `safe = amount * (1 - buffer_bps / 10_000)`

---

## 5. Opportunity validation

**Primary files**
- `python/apex_omega_core/core/inference.py`
- `python/apex_omega_core/core/execution_engine.py`

**Function index**
- `profitability_gate()`
- `derive_net_edge()`
- `ExecutionEngine.validate_opportunity()`

**Variable / data index**
- `buy_price`, `buy_slippage`
- `sell_price`, `sell_slippage`
- `ml_slippage`, `raw_spread`, `buffer_rate`, `trade_size`, `fees`
- `money_in`, `money_out`, `edge`, `adjusted_slippage`, `ev_buffer`, `net_edge`
- `p_fill`
- Opportunity keys: `net_profit_usd`, `slippage_bps`, `pool_tvl_usd`
- Runtime thresholds: `min_net_profit_usd`, `max_route_slippage_bps`, `min_pool_tvl_usd`

**Math index**
- `money_out = buy_price + buy_slippage`
- `money_in = sell_price - sell_slippage`
- `edge = money_in - money_out`
- `adjusted_slippage = ml_slippage / 3`
- `ev_buffer = raw_spread * buffer_rate * (trade_size / 100_000)`
- `net_edge = edge - adjusted_slippage - ev_buffer - fees`
- Execution gate: `p_net > 0 and p_fill > 0`

---

## 6. Mempool degradation check

**Primary files**
- `python/apex_omega_core/core/mempool_simulator.py`
- `python/apex_omega_core/core/slippage_sentinel.py`

**Function index**
- `MempoolSimulator.evaluate()`
- `MempoolSimulator.assert_safe()`
- `SlippageSentinel.mempool_validate()`
- `SlippageSentinel.MempoolSimulator.apply_pending_tx()`
- `SlippageSentinel.MempoolSimulator.simulate_with_mempool()`

**Variable / data index**
- `MempoolImpact.{original_output,adjusted_output,degradation_bps,safe}`
- `max_degradation_bps`
- `haircut_bps`
- `pending_txs`
- `threshold`
- `decision` (`SAFE` / `ABORT`)

**Math index**
- Conservative haircut: `adjusted = original * (1 - haircut_bps / 10_000)`
- Degradation: `((original - adjusted) / original) * 10_000`
- Sentinel decision: `SAFE if final_out >= original_output * threshold else ABORT`

---

## 7. Lane reservation

**Primary files**
- `python/apex_omega_core/core/lane_manager.py`

**Function index**
- `sync()`
- `reserve_lane()`
- `mark_submitted()`
- `release_lane()`
- `snapshot()`

**Variable / data index**
- `LaneState.{lane_id,next_nonce,active,last_tx_hash,metadata}`
- `NonceLaneManager.{lane_count,_base_nonce,_cursor,_lanes}`
- `opportunity_id`

**Math index**
- Initial lane nonce: `lane.next_nonce = base_nonce + offset`
- Release step: `lane.next_nonce += lane_count`

---

## 8. C1 execution plan

**Primary files**
- `python/apex_omega_core/core/live_strategy_steps.py`
- `python/apex_omega_core/core/scanner_strategy_pipeline.py`
- `python/apex_omega_core/core/execution_engine.py`
- `python/apex_omega_core/execution/pre_execution_pipeline.py`

**Function index**
- `build_live_strategy_output_from_state()`
- `_build_v2_dynamic_candidate()`
- `run_scanner_strategy_pipeline()`
- `ExecutionEngine.build_c1_plan()`
- `canonical_execution_pipeline()` (C1 branch)

**Variable / data index**
- Strategy output keys:
  - `asset`
  - `min_profit`
  - `gas_reserve_asset`
  - `dex_fee_reserve_asset`
  - `steps`
  - `opportunity`
- `LiveStrategyBuildResult.{strikeable,reason,strategy_output,compiled_payload_len,min_profit,diagnostics}`
- `ExecutionPlan.{target,compiled,calldata,flash_loan_amount}`
- `C1_BUILD_PAYLOAD`, `C1_REJECT`

**Math index**
- Flash fee: `amount_in * (flash_fee_bps / 10_000)`
- Owner submission edge in strategy build: `net_profit - gas_cost_usd`
- Raw-unit conversion: `int(amount * 10^decimals)`

---

## 9. Calldata compilation

**Primary files**
- `python/apex_omega_core/core/execution_compiler.py`

**Function index**
- `EnvelopeCompiler.encode_institutional_step()`
- `EnvelopeCompiler.encode_ultimate_step()`
- `EnvelopeCompiler.build_institutional_envelope()`
- `EnvelopeCompiler.build_ultimate_envelope()`
- `FlashloanPayloadBuilder.build_aave_payload()`
- `FlashloanPayloadBuilder.build_balancer_payload()`
- `ExecutionCompiler.compile_for_institutional()`
- `ExecutionCompiler.compile_for_ultimate()`
- `ExecutionCompiler.merkle_leaf()`
- `compile_strategy_batch()`

**Variable / data index**
- `INSTITUTIONAL_STEP_TYPE`
- `ULTIMATE_STEP_TYPE`
- `CompiledExecution.{encoded_payload,min_profit,asset}`
- Route envelope keys:
  - `version`
  - `profitToken`
  - `gasReserveAsset`
  - `dexFeeReserveAsset`
  - `steps`
- Step keys:
  - `protocol`
  - `target`
  - `approveToken`
  - `outputToken`
  - `callValue`
  - `minAmountIn`
  - `minAmountOut`
  - `feeBps`
  - `data`

**Math index**
- Institutional envelope ABI: `(version, profitToken, gasReserveAsset, dexFeeReserveAsset, steps[])`
- Ultimate envelope ABI: `(version, profitToken, gasReserveAsset, dexFeeReserveAsset, steps[])`
- Merkle leaf: `keccak(encoded_payload)`

---

## 10. Signing

**Primary files**
- `python/apex_omega_core/core/execution_engine.py`
- `python/apex_omega_core/core/runtime_config.py`

**Function index**
- `ExecutionEngine._selector()`
- `ExecutionEngine._flash_loan_amount()`
- `ExecutionEngine._merkle_proof()`
- `ExecutionEngine.sign_transaction()`
- `RuntimeConfig.assert_safe_to_send()`

**Variable / data index**
- `RuntimeConfig.{chain_id,executor_private_key,c1_executor_address,c2_executor_address,bundle_target_block_offset}`
- `RuntimeConfig.primary_rpc`
- `plan.target`
- Transaction fields:
  - `to`
  - `data`
  - `chainId`
  - `nonce`
  - `gas`
  - `maxFeePerGas`
  - `maxPriorityFeePerGas`

**Math index**
- Function selector: `keccak(signature)[:4]`
- Derived flash-loan amount: explicit amount or first step `minAmountIn`

---

## 11. Private bundle submission

**Primary files**
- `python/apex_omega_core/core/relay_submitter.py`
- `python/apex_omega_core/core/execution_engine.py`
- `python/apex_omega_core/core/runtime_config.py`

**Function index**
- `RelayBundleSubmitter.build_eth_send_bundle_payload()`
- `RelayBundleSubmitter.submit_bundle()`
- `RelayBundleSubmitter.dry_run_payload()`
- `ExecutionEngine.execute_bundle()`

**Variable / data index**
- `BundleSubmissionResult.{relay,url,status,latency_ms,response,error}`
- `RuntimeConfig.relays`
- `raw_txs`
- `target_block`

**Math index**
- Target block: `current_block + bundle_target_block_offset`
- RPC payload block number: `hex(target_block)`

---

## 12. C1 inclusion observation

**Primary files**
- `python/apex_omega_core/core/c2_trigger_system.py`

**Function index**
- `await_c1_receipt()`

**Variable / data index**
- `tx_hash`
- `timeout_blocks`
- `start_block`
- `receipt`

**Math index**
- Observation window is discrete block polling over `timeout_blocks`.

---

## 13. Post-C1 pool state refresh

**Primary files**
- `python/apex_omega_core/core/c2_trigger_system.py`
- `python/apex_omega_core/execution/pre_execution_pipeline.py`

**Function index**
- `state_fetcher(receipt)` - injected callback in `C2TriggerSystem`
- `reload_state_fn(candidate, c1_result, c1_execution_result)` - injected callback in `canonical_execution_pipeline()`

**Variable / data index**
- `receipt`
- `post_state`
- `post_c1_state`
- `c1_execution_result`

**Math index**
- No new math; this stage establishes the post-C1 state boundary used by C2.

---

## 14. C2 recomputation

**Primary files**
- `python/apex_omega_core/core/c2_trigger_system.py`
- `python/apex_omega_core/execution/pre_execution_pipeline.py`
- `python/apex_omega_core/core/execution_engine.py`

**Function index**
- `strategy_builder(post_state)`
- `c2_fn(post_c1_state)`
- `ExecutionEngine.build_c2_plan()`
- `canonical_execution_pipeline()` (C2 branch)

**Variable / data index**
- `C2Decision.{action,reason,plan}`
- `post_state`
- `c2_result`
- `merkle_proof`
- `merkle_leaf`

**Math index**
- C2 reuses the same opportunity and execution threshold math, but only against refreshed post-C1 state.

---

## 15. C2 validation

**Primary files**
- `python/apex_omega_core/core/c2_trigger_system.py`
- `python/apex_omega_core/core/execution_engine.py`
- `python/apex_omega_core/execution/pre_execution_pipeline.py`

**Function index**
- `ExecutionEngine.validate_opportunity()`
- `_canon_fork_validate()`
- `_canon_get_action()`
- `_canon_get_reason()`

**Variable / data index**
- `C2_EXECUTE`, `C2_NO_OP`, `C2_ALLOWED_DECISIONS`
- `c2_decision`
- `c2_fork_passed`
- `c2_fork_result`
- `allowed_c2_decisions`

**Math index**
- Validation gates remain threshold comparisons:
  - `net_profit_usd >= min_net_profit_usd`
  - `slippage_bps <= max_route_slippage_bps`
  - `pool_tvl_usd >= min_pool_tvl_usd`

---

## 16. C2 execution / idle decision

**Primary files**
- `python/apex_omega_core/core/c2_trigger_system.py`
- `python/apex_omega_core/execution/pre_execution_pipeline.py`

**Function index**
- `C2TriggerSystem.decide_and_build()`
- `canonical_execution_pipeline()`

**Variable / data index**
- `action` (`execute` / `idle`)
- `reason`
- `terminal_state`
- `accepted`
- `c2_execution_result`
- `events`

**Math index**
- Idle path is chosen whenever residual EV fails validation or C1 never reaches a valid inclusion state.
- Execute path is chosen only after post-C1 opportunity validation and separate C2 fork validation both pass.

---

## 17. Audit log

**Primary files**
- `python/apex_omega_core/execution/post_block_audit.py`

**Function index**
- `audit_post_block()`
- `PostBlockAudit.as_log_record()`
- `PredictionErrorRollup.record()`

**Variable / data index**
- `PostBlockAudit.{predicted_reserve0,predicted_reserve1,actual_reserve0,actual_reserve1,expected_out,actual_out,expected_profit,realized_profit}`
- `PredictionErrorRollup.{samples,total_error_bps,risk_buffer_bps,tighten_threshold_bps,loosen_threshold_bps,adjustment_bps}`

**Math index**
- `reserve_error_bps = max(|pred0-actual0|/max(|pred0|,1), |pred1-actual1|/max(|pred1|,1)) * 10_000`
- `output_error_bps = |expected_out-actual_out| / max(|expected_out|,1) * 10_000`
- `profit_error_bps = |expected_profit-realized_profit| / max(|expected_profit|,1) * 10_000`
- `prediction_error_bps = max(reserve_error_bps, output_error_bps, profit_error_bps)`
- Rollup adjustment:
  - increase `risk_buffer_bps` by `adjustment_bps` when error exceeds `tighten_threshold_bps`
  - decrease `risk_buffer_bps` by `adjustment_bps` when error is below `loosen_threshold_bps`

---

## Canonical execution micro-stage index

`python/apex_omega_core/execution/pre_execution_pipeline.py` also defines the locked micro-stage sequence in `CANONICAL_EXECUTION_FLOW`:

1. `scanner`
2. `gates`
3. `C1`
4. `fork_sim_c1`
5. `execute_or_shadow_c1`
6. `reload_or_shadow_mutate_state`
7. `C2`
8. `fork_sim_c2`
9. `execute_or_no_op_c2`
10. `logs_dashboard`

Use this micro-flow when tracing the strict C1-before-C2 execution contract; use the stage sections above when tracing the larger discovery-to-audit pipeline.
