
from apex_omega_core.core.runtime_config import load_runtime_config
from apex_omega_core.core.rpc_tester import get_canonical_two_leg_state
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.live_strategy_steps import build_live_strategy_output_from_state

config = load_runtime_config()
sentinel = SlippageSentinel()

state = get_canonical_two_leg_state()
fee1 = float(state["fee1"])
r1_in = float(state["r1_in"])
r1_out = float(state["r1_out"])
fee2 = float(state["fee2"])
r2_in = float(state["r2_in"])
r2_out = float(state["r2_out"])

amount_in = sentinel.optimal_two_leg_input(r1_in, r1_out, fee1, r2_in, r2_out, fee2)
flash_fee = amount_in * (config.flash_loan_fee_bps / 10_000.0)
result = sentinel.two_leg_arb_profit(
    amount_in,
    fee1, r1_in, r1_out,
    fee2, r2_in, r2_out,
    c_gas=0.0,
    c_loan=flash_fee,
    c_other=config.risk_buffer_usd,
)
owner_submission_edge = result["p_net"] - config.c2_gas_usd

print("\n=== APEX-OMEGA LIVE MARKET DRY RUN ===")
print(f"chain_id: {config.chain_id}")
print(f"live_trading_enabled: {config.live_trading_enabled}")
print(f"dry_run: {config.dry_run}")
print("\n--- Pool State ---")
print(f"rpc_url: {state.get('rpc_url', '')}")
print(f"fee1: {fee1}")
print(f"r1_in: {r1_in}")
print(f"r1_out: {r1_out}")
print(f"fee2: {fee2}")
print(f"r2_in: {r2_in}")
print(f"r2_out: {r2_out}")
for key in ["qsv2_usdc_per_wmatic", "uv3_usdc_per_wmatic", "raw_spread_usdc_per_wmatic", "raw_spread_bps"]:
    if key in state:
        print(f"{key}: {state[key]}")
print("\n--- Leg Math ---")
print(f"optimal_amount_in: {amount_in}")
print(f"leg1_b_out: {result['b_out_1']}")
print(f"leg2_a_out: {result['a_out_2']}")
print("\n--- Costs / PnL ---")
print(f"gross_profit: {result['p_gross']}")
print(f"flash_fee: {flash_fee}")
print(f"gas_cost: {config.c2_gas_usd}")
print(f"risk_buffer: {config.risk_buffer_usd}")
print(f"net_profit: {result['p_net']}")
print(f"owner_submission_edge: {owner_submission_edge}")
print("\n--- Decision ---")
print("DECISION: STRIKE" if result["p_net"] > config.min_net_profit_usd else "DECISION: IDLE")

strategy_build = build_live_strategy_output_from_state(
    state,
    executor_address=config.c1_executor_address or "0x0000000000000000000000000000000000000000",
    min_net_profit_usd=config.min_net_profit_usd,
    gas_cost_usd=config.c1_gas_usd,
    flash_fee_bps=config.flash_loan_fee_bps,
    risk_buffer_usd=config.risk_buffer_usd,
)
print("\n=== STRATEGY BUILD ===")
print("strikeable:", strategy_build.strikeable)
print("reason:", strategy_build.reason)
if strategy_build.diagnostics:
    for key, value in strategy_build.diagnostics.items():
        print(f"{key}: {value}")
if strategy_build.strategy_output:
    print("steps:", len(strategy_build.strategy_output["steps"]))
    print("payload_len:", strategy_build.compiled_payload_len)
    print("min_profit:", strategy_build.min_profit)
