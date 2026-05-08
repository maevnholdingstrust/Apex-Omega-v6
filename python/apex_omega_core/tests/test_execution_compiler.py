from eth_abi import decode

from apex_omega_core.core.execution_compiler import (
    EnvelopeCompiler,
    ExecutionCompiler,
    FlashloanPayloadBuilder,
)
from apex_omega_core.core.protocol_swaps import (
    PROTOCOL_ALGEBRA,
    PROTOCOL_BALANCER,
    PROTOCOL_CURVE,
    PROTOCOL_UNISWAP_V2,
    PROTOCOL_UNISWAP_V3,
    ProtocolSwapEncoder,
    min_amount_out_from_quote,
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


def test_min_amount_out_from_quote():
    assert min_amount_out_from_quote(10_000, 50) == 9_950


def test_protocol_swap_encoder_uniswap_v2():
    step = {
        "protocol": PROTOCOL_UNISWAP_V2,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "recipient": "0x4444444444444444444444444444444444444444",
        "amountIn": 1_000,
        "amountOutQuote": 990,
        "slippageBps": 30,
    }
    data = ProtocolSwapEncoder.resolve_step_data(step)
    assert isinstance(data, bytes)
    assert data[:4].hex() == "38ed1739"


def test_protocol_swap_encoder_uniswap_v3():
    step = {
        "protocol": PROTOCOL_UNISWAP_V3,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "poolFee": 500,
        "recipient": "0x4444444444444444444444444444444444444444",
        "amountIn": 1_000,
        "amountOutMin": 980,
    }
    data = ProtocolSwapEncoder.resolve_step_data(step)
    assert data[:4].hex() == "414bf389"
    decoded = decode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        data[4:],
    )[0]
    assert decoded[0] == "0x2222222222222222222222222222222222222222"
    assert decoded[1] == "0x3333333333333333333333333333333333333333"
    assert decoded[2] == 500
    assert decoded[3] == "0x4444444444444444444444444444444444444444"
    assert decoded[5] == 1_000
    assert decoded[6] == 980


def test_protocol_swap_encoder_algebra():
    step = {
        "protocol": PROTOCOL_ALGEBRA,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "recipient": "0x4444444444444444444444444444444444444444",
        "amountIn": 1_000,
        "amountOutMin": 980,
    }
    data = ProtocolSwapEncoder.resolve_step_data(step)
    assert data[:4].hex() == "bc651188"
    decoded = decode(
        ["(address,address,address,uint256,uint256,uint256,uint160)"],
        data[4:],
    )[0]
    assert decoded[0] == "0x2222222222222222222222222222222222222222"
    assert decoded[1] == "0x3333333333333333333333333333333333333333"
    assert decoded[2] == "0x4444444444444444444444444444444444444444"
    assert decoded[4] == 1_000
    assert decoded[5] == 980


def test_protocol_swap_encoder_curve():
    step = {
        "protocol": PROTOCOL_CURVE,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "pool": "0x5555555555555555555555555555555555555555",
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "recipient": "0x4444444444444444444444444444444444444444",
        "amountIn": 1_000,
        "amountOutMin": 980,
    }
    data = ProtocolSwapEncoder.resolve_step_data(step)
    assert data[:4].hex() == "1a4c1ca3"
    decoded = decode(
        ["address", "address", "address", "uint256", "uint256", "address"],
        data[4:],
    )
    assert decoded[0] == "0x5555555555555555555555555555555555555555"
    assert decoded[1] == "0x2222222222222222222222222222222222222222"
    assert decoded[2] == "0x3333333333333333333333333333333333333333"
    assert decoded[3] == 1_000
    assert decoded[4] == 980
    assert decoded[5] == "0x4444444444444444444444444444444444444444"


def test_protocol_swap_encoder_balancer():
    step = {
        "protocol": PROTOCOL_BALANCER,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "poolId": "0x" + "11" * 32,
        "recipient": "0x4444444444444444444444444444444444444444",
        "amountIn": 1_000,
        "amountOutMin": 980,
    }
    data = ProtocolSwapEncoder.resolve_step_data(step)
    assert data[:4].hex() == "52bbbe29"
    decoded = decode(
        [
            "(bytes32,uint8,address,address,uint256,bytes)",
            "(address,bool,address,bool)",
            "uint256",
            "uint256",
        ],
        data[4:],
    )
    assert decoded[0][1] == 0
    assert decoded[0][4] == 1_000
    assert decoded[1][0] == "0x4444444444444444444444444444444444444444"
    assert decoded[1][2] == "0x4444444444444444444444444444444444444444"
    assert decoded[2] == 980


def test_envelope_compiler_builds_data_from_protocol_fields():
    compiler = EnvelopeCompiler()
    step = {
        "protocol": PROTOCOL_UNISWAP_V2,
        "target": "0x1111111111111111111111111111111111111111",
        "approveToken": "0x2222222222222222222222222222222222222222",
        "outputToken": "0x3333333333333333333333333333333333333333",
        "recipient": "0x4444444444444444444444444444444444444444",
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "amountIn": 1_000,
        "amountOutQuote": 990,
        "slippageBps": 30,
    }
    encoded = compiler.encode_institutional_step(step)
    assert encoded[5] == 1_000
    assert encoded[6] == 987
    assert isinstance(encoded[8], bytes)
    assert encoded[8][:4].hex() == "38ed1739"
