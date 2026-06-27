from apex_omega_core.core.dex_execution_adapters import (
    DexFamily,
    LiveLegQuote,
    build_vm_steps_from_live_quotes,
    validate_round_trip_steps,
)

A = "0x0000000000000000000000000000000000000001"
B = "0x0000000000000000000000000000000000000002"
V2_ROUTER = "0x0000000000000000000000000000000000000003"
V3_ROUTER = "0x0000000000000000000000000000000000000004"
RECEIVER = "0x0000000000000000000000000000000000000005"


def test_builds_mixed_v2_v3_round_trip_steps():
    legs = [
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
    ]
    steps = build_vm_steps_from_live_quotes(legs)
    validate_round_trip_steps(steps, A)
    assert len(steps) == 2
    assert steps[0].payload.startswith("0x")
    assert steps[1].payload.startswith("0x")
    assert steps[0].tokenOut.lower() == steps[1].tokenIn.lower()
