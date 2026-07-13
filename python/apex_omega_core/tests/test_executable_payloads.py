import pytest

from apex_omega_core.core.executable_payloads import (
    PAYLOAD_KIND_C1,
    STATUS_EXECUTABLE,
    STATUS_LOCKED,
    FlashloanIntegratedC1Payload,
    VmExecutionContext,
    VmStep,
)

A = "0x0000000000000000000000000000000000000001"
B = "0x0000000000000000000000000000000000000002"
T = "0x0000000000000000000000000000000000000003"


def _payload(final_token=A):
    return FlashloanIntegratedC1Payload(
        payloadKind=PAYLOAD_KIND_C1,
        redisId="r1",
        targetContract=T,
        chainId=137,
        routeId="route-1",
        candidateStatus=STATUS_EXECUTABLE,
        lockStatus=STATUS_LOCKED,
        flashloanSource=1,
        flashloanAsset=A,
        flashloanAmount=1000,
        context=VmExecutionContext(
            profitAsset=A,
            minNetProfit=1,
            nonce=1,
            steps=[
                VmStep(venue=T, tokenIn=A, tokenOut=B, amountIn=1000, minAmountOut=900, payload="0x01"),
                VmStep(venue=T, tokenIn=B, tokenOut=final_token, amountIn=900, minAmountOut=1001, payload="0x02"),
            ],
        ),
        grossProfitUsd=10.0,
        flashFeeUsd=1.0,
        gasCostUsd=1.0,
        riskBufferUsd=1.0,
        netProfitUsd=7.0,
        leg1BuyPrice=1.0,
        leg2SellPrice=1.01,
        deadlineBlock=999999999,
    )


def test_payload_requires_round_trip():
    _payload().assert_route_invariants()


def test_payload_rejects_non_round_trip():
    with pytest.raises(ValueError):
        _payload(final_token=B).assert_route_invariants()
