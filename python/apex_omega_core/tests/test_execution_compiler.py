from eth_abi import decode
from web3 import Web3

from apex_omega_core.core.execution_compiler import (
    EnvelopeCompiler,
    ExecutionCompiler,
    FlashloanPayloadBuilder,
)
from apex_omega_core.core.contract_invoker import ContractInvoker
from apex_omega_core.core.route_step_encoder import (
    SUSHISWAP_V2_ROUTER,
    build_uniswap_v2_like_step,
    validate_route_steps,
)


def _sample_institutional_step() -> dict:
    return {
        "protocol": 1,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "callValue": 0,
        "minAmountIn": 1000,
        "minAmountOut": 990,
        "feeBps": 30,
        "data": b"\x12\x34\x56\x78",
    }


def _sample_ultimate_step() -> dict:
    return {
        "protocol": 5,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "callValue": 0,
        "minAmountIn": 2000,
        "minAmountOut": 1980,
        "feeBps": 20,
        "data": b"\xab\xcd\xef\x01",
    }


def test_build_institutional_envelope_roundtrip():
    compiler = EnvelopeCompiler()
    route = {
        "version": 1,
        "profitToken": "0x4444444444444444444444444444444444444444",
        "gasReserveAsset": 7,
        "dexFeeReserveAsset": 11,
        "steps": [_sample_institutional_step()],
    }

    encoded = compiler.build_institutional_envelope(route)
    decoded = decode(
        [
            "uint8",
            "address",
            "uint256",
            "uint256",
            "(uint8,address,address,address,uint256,uint256,uint256,uint16,bytes)[]",
        ],
        encoded,
    )

    assert decoded[0] == 1
    assert decoded[2] == 7
    assert decoded[3] == 11
    assert len(decoded[4]) == 1
    assert decoded[4][0][0] == 1
    assert decoded[4][0][7] == 30


def test_build_ultimate_envelope_roundtrip():
    compiler = EnvelopeCompiler()
    route = {
        "version": 1,
        "profitToken": "0x5555555555555555555555555555555555555555",
        "gasReserveAsset": 3,
        "dexFeeReserveAsset": 9,
        "steps": [_sample_ultimate_step()],
    }

    encoded = compiler.build_ultimate_envelope(route)
    decoded = decode(
        [
            "uint8",
            "address",
            "uint256",
            "uint256",
            "(uint8,address,address,uint256,uint256,uint256,uint16,bytes)[]",
        ],
        encoded,
    )

    assert decoded[0] == 1
    assert decoded[2] == 3
    assert decoded[3] == 9
    assert len(decoded[4]) == 1
    assert decoded[4][0][0] == 5
    assert decoded[4][0][6] == 20


def test_flashloan_payload_builders_roundtrip():
    route_payload = b"route-payload"

    aave_payload = FlashloanPayloadBuilder.build_aave_payload(123, route_payload)
    aave_decoded = decode(["uint256", "bytes"], aave_payload)
    assert aave_decoded[0] == 123
    assert aave_decoded[1] == route_payload

    bal_payload = FlashloanPayloadBuilder.build_balancer_payload(
        "0x6666666666666666666666666666666666666666", 10_000, 321, route_payload
    )
    bal_decoded = decode(["address", "uint256", "uint256", "bytes"], bal_payload)
    assert bal_decoded[1] == 10_000
    assert bal_decoded[2] == 321
    assert bal_decoded[3] == route_payload


def test_execution_compiler_compile_for_institutional():
    compiler = ExecutionCompiler()
    strategy_output = {
        "asset": "0x7777777777777777777777777777777777777777",
        "min_profit": 42,
        "steps": [_sample_institutional_step()],
    }

    compiled = compiler.compile_for_institutional(strategy_output)

    assert compiled.min_profit == 42
    assert compiled.asset == "0x7777777777777777777777777777777777777777"
    assert isinstance(compiled.encoded_payload, bytes)
    assert len(compiled.encoded_payload) > 0

    leaf = compiler.merkle_leaf(compiled.encoded_payload)
    assert isinstance(leaf, bytes)
    assert len(leaf) == 32


def test_execution_compiler_rejects_missing_router_calldata():
    compiler = ExecutionCompiler()
    step = _sample_institutional_step()
    step["data"] = b"\x00"
    strategy_output = {
        "asset": "0x7777777777777777777777777777777777777777",
        "min_profit": 42,
        "steps": [step],
    }

    try:
        compiler.compile_for_institutional(strategy_output)
    except ValueError as exc:
        assert "generated router calldata" in str(exc)
    else:
        raise AssertionError("compiler accepted missing router calldata")


def test_envelope_compiler_accepts_mixed_step_key_styles():
    compiler = EnvelopeCompiler()

    # Institutional: snake_case only (no camelCase keys)
    inst_step = {
        "protocol": 1,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "callValue": 0,
        "min_amount_in": 1000,
        "min_amount_out": 990,
        "fee_bps": 30,
        "data": b"\x12\x34\x56\x78",
    }
    inst_route = {
        "version": 1,
        "profitToken": "0x4444444444444444444444444444444444444444",
        "gasReserveAsset": 0,
        "dexFeeReserveAsset": 0,
        "steps": [inst_step],
    }
    encoded = compiler.build_institutional_envelope(inst_route)
    decoded = decode(
        [
            "uint8",
            "address",
            "uint256",
            "uint256",
            "(uint8,address,address,address,uint256,uint256,uint256,uint16,bytes)[]",
        ],
        encoded,
    )
    assert decoded[4][0][5] == 1000
    assert decoded[4][0][6] == 990
    assert decoded[4][0][7] == 30

    # Ultimate: mixed — camelCase minAmountIn/Out, snake_case fee_bps
    ult_step = {
        "protocol": 5,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "callValue": 0,
        "minAmountIn": 2000,
        "minAmountOut": 1980,
        "fee_bps": 30,
        "data": b"\xab\xcd\xef\x01",
    }
    ult_route = {
        "version": 1,
        "profitToken": "0x5555555555555555555555555555555555555555",
        "gasReserveAsset": 0,
        "dexFeeReserveAsset": 0,
        "steps": [ult_step],
    }
    encoded = compiler.build_ultimate_envelope(ult_route)
    decoded = decode(
        [
            "uint8",
            "address",
            "uint256",
            "uint256",
            "(uint8,address,address,uint256,uint256,uint256,uint16,bytes)[]",
        ],
        encoded,
    )
    assert decoded[4][0][4] == 2000
    assert decoded[4][0][5] == 1980
    assert decoded[4][0][6] == 30


def test_v2_registry_step_to_payload_e2e_does_not_sign_or_submit(monkeypatch):
    monkeypatch.setenv("APEX_SEND_TX", "0")
    monkeypatch.delenv("APEX_PRIVATE_KEY", raising=False)
    signing_attempts = []

    step = build_uniswap_v2_like_step(
        router_name="sushiswap_v2",
        token_in="0x2222222222222222222222222222222222222222",
        token_out="0x3333333333333333333333333333333333333333",
        amount_in=1000,
        min_amount_out=990,
        recipient="0x4444444444444444444444444444444444444444",
        deadline=1_900_000_000,
    )

    expected_selector = Web3.keccak(
        text="swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"
    )[:4]
    assert step["target"] == Web3.to_checksum_address(SUSHISWAP_V2_ROUTER)
    assert step["data"][:4] == expected_selector

    validate_route_steps([step])

    strategy_output = {
        "asset": "0x2222222222222222222222222222222222222222",
        "min_profit": 1,
        "steps": [step],
    }
    compiled = ExecutionCompiler().compile_for_institutional(strategy_output)
    flash_payload = FlashloanPayloadBuilder.build_aave_payload(
        compiled.min_profit,
        compiled.encoded_payload,
    )

    assert isinstance(compiled.encoded_payload, bytes)
    assert len(compiled.encoded_payload) > 0
    assert len(flash_payload) > len(compiled.encoded_payload)
    assert Web3.to_checksum_address(step["target"]) == Web3.to_checksum_address(SUSHISWAP_V2_ROUTER)

    invoker = ContractInvoker.__new__(ContractInvoker)
    invoker.target_address = "0x9999999999999999999999999999999999999999"
    invoker.send_tx = False
    invoker.account = object()

    def fake_eth_call(calldata: str) -> dict:
        return {"ok": True, "output": calldata, "error": None}

    def forbidden_sign(*_args, **_kwargs) -> None:
        signing_attempts.append("sign")
        raise AssertionError("signing/submission path must not run in dry mode")

    invoker._eth_call = fake_eth_call
    invoker.w3 = type(
        "NoSubmitWeb3",
        (),
        {"eth": type("Eth", (), {"account": type("Account", (), {"sign_transaction": forbidden_sign})()})()},
    )()

    result = invoker.invoke(Web3.to_hex(flash_payload), p_net_usd=0.0)

    assert result["success"] is True
    assert result["simulation_only"] is True
    assert result["executed_onchain"] is False
    assert result["tx_hash"] is None
    assert result["broadcast"] == {"status": "not_sent", "reason": "APEX_SEND_TX != 1"}
    assert signing_attempts == []
