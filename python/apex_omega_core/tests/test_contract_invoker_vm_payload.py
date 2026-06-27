from apex_omega_core.core.contract_invoker import ContractInvoker
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


def _payload():
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
                VmStep(venue=T, tokenIn=B, tokenOut=A, amountIn=900, minAmountOut=1001, payload="0x02"),
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


def test_contract_invoker_builds_vm_payload_calldata():
    payload = _payload()
    record = {
        "redisId": payload.redisId,
        "status": STATUS_LOCKED,
        "candidateStatus": STATUS_EXECUTABLE,
        "flashloanAsset": payload.flashloanAsset,
        "flashloanAmount": payload.flashloanAmount,
        "opportunityHash": payload.opportunity_hash(),
    }
    invoker = ContractInvoker(T, rpc_url="http://127.0.0.1:8545")
    calldata = invoker.build_c1_calldata({"vm_payload": payload, "lock_record": record})
    assert calldata.startswith("0x")
    assert len(calldata) > 10
