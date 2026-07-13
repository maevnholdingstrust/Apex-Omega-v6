import pytest

from apex_omega_core.core.balancer_v3_vault_adapter import (
    BalancerSwapKind,
    BalancerV3Settlement,
    BalancerV3UnlockPlan,
    BalancerV3VaultSwap,
    build_balancer_v3_unlock_step,
    build_settle_payloads,
    encode_balancer_v3_settle,
    encode_balancer_v3_swap,
    encode_balancer_v3_unlock,
)

VAULT = "0x0000000000000000000000000000000000000001"
POOL = "0x0000000000000000000000000000000000000002"
A = "0x0000000000000000000000000000000000000003"
B = "0x0000000000000000000000000000000000000004"


def test_encodes_unlock_and_settle_payloads():
    unlock = encode_balancer_v3_unlock(b"callback")
    settle = encode_balancer_v3_settle(A, 1000)
    assert unlock.startswith("0x")
    assert settle.startswith("0x")
    assert unlock != settle


def test_encodes_vault_swap_payload():
    payload = encode_balancer_v3_swap(
        BalancerV3VaultSwap(
            pool=POOL,
            token_in=A,
            token_out=B,
            amount_given_raw=1000,
            limit_raw=900,
            kind=BalancerSwapKind.EXACT_IN,
        )
    )
    assert payload.startswith("0x")


def test_unlock_plan_requires_settlement_for_touched_tokens():
    plan = BalancerV3UnlockPlan(
        vault=VAULT,
        callback_data=b"callback",
        token_in=A,
        token_out=B,
        amount_in=1000,
        min_amount_out=900,
        swaps=[BalancerV3VaultSwap(pool=POOL, token_in=A, token_out=B, amount_given_raw=1000, limit_raw=900)],
        settlements=[BalancerV3Settlement(token=A, amount_hint=1000)],
    )
    with pytest.raises(ValueError, match="missing settlement"):
        build_balancer_v3_unlock_step(plan)


def test_builds_balancer_unlock_vm_step_with_settlements():
    plan = BalancerV3UnlockPlan(
        vault=VAULT,
        callback_data=b"callback",
        token_in=A,
        token_out=B,
        amount_in=1000,
        min_amount_out=900,
        swaps=[BalancerV3VaultSwap(pool=POOL, token_in=A, token_out=B, amount_given_raw=1000, limit_raw=900)],
        settlements=[BalancerV3Settlement(token=A, amount_hint=1000), BalancerV3Settlement(token=B, amount_hint=900)],
    )
    step = build_balancer_v3_unlock_step(plan)
    assert step.venue.lower() == VAULT.lower()
    assert step.payload.startswith("0x")
    assert len(build_settle_payloads(plan.settlements)) == 2
