from apex_omega_core.core.dex_execution_adapters import DexFamily, LiveLegQuote
from apex_omega_core.core.executable_opportunity_builder import ExecutableOpportunityInput, build_flashloan_c1_payload
from apex_omega_core.core.executable_payloads import PAYLOAD_KIND_C1

A = "0x0000000000000000000000000000000000000001"
B = "0x0000000000000000000000000000000000000002"
TARGET = "0x0000000000000000000000000000000000000003"
V2_ROUTER = "0x0000000000000000000000000000000000000004"
V3_ROUTER = "0x0000000000000000000000000000000000000005"
RECEIVER = "0x0000000000000000000000000000000000000006"


def test_builds_c1_payload_from_v2_v3_quote_legs():
    inp = ExecutableOpportunityInput(
        redis_id="redis-1",
        target_contract=TARGET,
        route_id="route-1",
        chain_id=137,
        flashloan_source=1,
        flashloan_asset=A,
        flashloan_amount=1000,
        profit_asset=A,
        min_net_profit=1,
        nonce=7,
        deadline_block=999999999,
        gross_profit_usd=10.0,
        flash_fee_usd=1.0,
        gas_cost_usd=2.0,
        risk_buffer_usd=1.0,
        net_profit_usd=6.0,
        leg1_buy_price=1.0,
        leg2_sell_price=1.01,
        legs=[
            LiveLegQuote(
                family=DexFamily.V2,
                venue=V2_ROUTER,
                token_in=A,
                token_out=B,
                amount_in=1000,
                min_amount_out=900,
                receiver=RECEIVER,
                deadline=999999999,
                quote_amount_out=950,
            ),
            LiveLegQuote(
                family=DexFamily.V3,
                venue=V3_ROUTER,
                token_in=B,
                token_out=A,
                amount_in=900,
                min_amount_out=1001,
                receiver=RECEIVER,
                deadline=999999999,
                fee=500,
                quote_amount_out=1010,
            ),
        ],
    )
    payload = build_flashloan_c1_payload(inp)
    assert payload.payloadKind == PAYLOAD_KIND_C1
    assert len(payload.context.steps) == 2
    assert payload.context.steps[0].tokenOut.lower() == payload.context.steps[1].tokenIn.lower()
    payload.assert_route_invariants()
