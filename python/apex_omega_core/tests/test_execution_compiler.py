from eth_abi import decode

from apex_omega_core.core.execution_compiler import (
    EnvelopeCompiler,
    ExecutionCompiler,
    FlashloanPayloadBuilder,
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
        "data": b"\xab\xcd",
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
