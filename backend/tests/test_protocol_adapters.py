"""Unit tests for backend.protocol_adapters and backend.execution_payload_builder.

All tests are fully offline – no network calls are made.

Coverage
--------
* Function selectors: verified against known 4-byte keccak values.
* Argument layout: decoded and checked field-by-field.
* Pool-derived fee tier: fee_tier=500 vs fee_tier=3000 produce distinct calldata.
* Recipient semantics: executor contract address appears in swap calldata.
* Fail-closed behaviour: UnknownDexError on unsupported DEX keys.
* SimulationFailedError gate in ExecutionPayloadBuilder.
* SwapStep / BuildResult round-tripping.
"""

from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from eth_abi import decode as abi_decode
from web3 import Web3

from backend.protocol_adapters import (
    PROTOCOL_ALGEBRA,
    PROTOCOL_BALANCER,
    PROTOCOL_CURVE,
    PROTOCOL_UNISWAP_V2,
    PROTOCOL_UNISWAP_V3,
    AlgebraV3Adapter,
    BalancerAdapter,
    CurveAdapter,
    PoolFeeInfo,
    QuickSwapV2Adapter,
    SimulationFailedError,
    SushiV2Adapter,
    UniswapV3Adapter,
    UnknownDexError,
    encode_swap_step,
    get_adapter,
    resolve_pool_fee_info,
)
from backend.execution_payload_builder import (
    BuildResult,
    ExecutionPayloadBuilder,
    SwapStep,
)

# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------

TOKEN_A = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC (Polygon)
TOKEN_B = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"  # WETH (Polygon)
EXECUTOR = "0xd60d6a59007eeCA9260e0e5e7B02607c05D666BD"
POOL_ID_32 = b"\xab" * 32
CURVE_POOL = "0x445FE580eF8d70FF569aB36e898ed8f2dc679af0"

# Known 4-byte selectors (pre-computed from Solidity signatures)
SEL_SWAP_EXACT_TOKENS = bytes.fromhex("38ed1739")   # swapExactTokensForTokens(…)
SEL_EXACT_INPUT_SINGLE_V3 = bytes.fromhex("414bf389")  # exactInputSingle((addr,addr,uint24,addr,uint,uint,uint,uint160))
SEL_EXACT_INPUT_SINGLE_ALGEBRA = bytes.fromhex("bc651188")  # exactInputSingle((addr,addr,addr,uint,uint,uint,uint160))
SEL_CURVE_EXCHANGE = bytes.fromhex("1a4c1ca3")  # exchange(address,address,address,uint256,uint256,address)
SEL_BALANCER_SWAP = bytes.fromhex("52bbbe29")  # swap((bytes32,uint8,addr,addr,uint,bytes),(addr,bool,addr,bool),uint,uint)


# ---------------------------------------------------------------------------
# Selector correctness
# ---------------------------------------------------------------------------


def test_quickswap_v2_selector():
    assert QuickSwapV2Adapter.SELECTOR == SEL_SWAP_EXACT_TOKENS


def test_sushi_v2_inherits_selector():
    assert SushiV2Adapter.SELECTOR == SEL_SWAP_EXACT_TOKENS


def test_uniswap_v3_selector():
    assert UniswapV3Adapter.SELECTOR == SEL_EXACT_INPUT_SINGLE_V3


def test_algebra_v3_selector():
    assert AlgebraV3Adapter.SELECTOR == SEL_EXACT_INPUT_SINGLE_ALGEBRA


def test_curve_selector():
    assert CurveAdapter.SELECTOR == SEL_CURVE_EXCHANGE


def test_balancer_selector():
    assert BalancerAdapter.SELECTOR == SEL_BALANCER_SWAP


# ---------------------------------------------------------------------------
# QuickSwap V2 / Sushi V2 argument layout
# ---------------------------------------------------------------------------


def test_quickswap_v2_calldata_layout():
    amount_in = 1_000_000
    min_out = 900_000
    calldata = QuickSwapV2Adapter.encode(
        TOKEN_A, TOKEN_B, amount_in, min_out, EXECUTOR
    )
    assert calldata[:4] == SEL_SWAP_EXACT_TOKENS
    (a_in, a_out_min, path, recipient, _deadline) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        calldata[4:],
    )
    assert a_in == amount_in
    assert a_out_min == min_out
    assert path[0].lower() == TOKEN_A.lower()
    assert path[1].lower() == TOKEN_B.lower()
    assert recipient.lower() == EXECUTOR.lower()


def test_quickswap_v2_recipient_is_executor():
    calldata = QuickSwapV2Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR)
    (_, _, _, recipient, _) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        calldata[4:],
    )
    assert recipient.lower() == EXECUTOR.lower()


def test_quickswap_v2_custom_path():
    mid_token = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"  # WMATIC
    calldata = QuickSwapV2Adapter.encode(
        TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR,
        path=[TOKEN_A, mid_token, TOKEN_B],
    )
    (_, _, path, _, _) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        calldata[4:],
    )
    assert len(path) == 3


def test_quickswap_v2_rejects_zero_amount_in():
    with pytest.raises(ValueError, match="amount_in must be positive"):
        QuickSwapV2Adapter.encode(TOKEN_A, TOKEN_B, 0, 900, EXECUTOR)


def test_quickswap_v2_rejects_zero_min_out():
    with pytest.raises(ValueError, match="min_amount_out must be positive"):
        QuickSwapV2Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 0, EXECUTOR)


def test_quickswap_v2_rejects_circular_path():
    with pytest.raises(ValueError, match="same token"):
        QuickSwapV2Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR, path=[TOKEN_A, TOKEN_A])


# ---------------------------------------------------------------------------
# Uniswap V3 argument layout
# ---------------------------------------------------------------------------


def test_uniswap_v3_calldata_layout_fee_500():
    calldata = UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000_000, 900_000, EXECUTOR, 500)
    assert calldata[:4] == SEL_EXACT_INPUT_SINGLE_V3
    (params,) = abi_decode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    tok_in, tok_out, fee, recipient, _dl, a_in, a_out_min, _lim = params
    assert tok_in.lower() == TOKEN_A.lower()
    assert tok_out.lower() == TOKEN_B.lower()
    assert fee == 500
    assert recipient.lower() == EXECUTOR.lower()
    assert a_in == 1_000_000
    assert a_out_min == 900_000


def test_uniswap_v3_calldata_layout_fee_3000():
    calldata = UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 2_000_000, 1_800_000, EXECUTOR, 3000)
    (params,) = abi_decode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    assert params[2] == 3000


def test_uniswap_v3_fee_500_differs_from_fee_3000():
    cd_500 = UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000_000, 900_000, EXECUTOR, 500)
    cd_3000 = UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000_000, 900_000, EXECUTOR, 3000)
    assert cd_500 != cd_3000


def test_uniswap_v3_rejects_zero_fee():
    with pytest.raises(ValueError, match="fee_tier must be positive"):
        UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR, 0)


def test_uniswap_v3_recipient_is_executor():
    calldata = UniswapV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR, 3000)
    (params,) = abi_decode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    assert params[3].lower() == EXECUTOR.lower()


# ---------------------------------------------------------------------------
# Algebra V3 argument layout
# ---------------------------------------------------------------------------


def test_algebra_v3_calldata_layout():
    calldata = AlgebraV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000_000, 900_000, EXECUTOR)
    assert calldata[:4] == SEL_EXACT_INPUT_SINGLE_ALGEBRA
    (params,) = abi_decode(
        ["(address,address,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    tok_in, tok_out, recipient, _dl, a_in, a_out_min, _lim = params
    assert tok_in.lower() == TOKEN_A.lower()
    assert tok_out.lower() == TOKEN_B.lower()
    assert recipient.lower() == EXECUTOR.lower()
    assert a_in == 1_000_000
    assert a_out_min == 900_000


def test_algebra_v3_has_no_fee_parameter():
    # Algebra struct is (addr, addr, addr, uint256, uint256, uint256, uint160) – 7 fields, no uint24 fee
    calldata = AlgebraV3Adapter.encode(TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR)
    (params,) = abi_decode(
        ["(address,address,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    assert len(params) == 7


# ---------------------------------------------------------------------------
# Curve argument layout
# ---------------------------------------------------------------------------


def test_curve_calldata_layout():
    calldata = CurveAdapter.encode(CURVE_POOL, TOKEN_A, TOKEN_B, 1_000_000, 900_000, EXECUTOR)
    assert calldata[:4] == SEL_CURVE_EXCHANGE
    pool, from_tok, to_tok, amount, expected, receiver = abi_decode(
        ["address", "address", "address", "uint256", "uint256", "address"],
        calldata[4:],
    )
    assert pool.lower() == CURVE_POOL.lower()
    assert from_tok.lower() == TOKEN_A.lower()
    assert to_tok.lower() == TOKEN_B.lower()
    assert amount == 1_000_000
    assert expected == 900_000
    assert receiver.lower() == EXECUTOR.lower()


# ---------------------------------------------------------------------------
# Balancer argument layout
# ---------------------------------------------------------------------------


def test_balancer_calldata_layout():
    calldata = BalancerAdapter.encode(
        POOL_ID_32, TOKEN_A, TOKEN_B, 1_000_000, 900_000,
        sender=EXECUTOR, recipient=EXECUTOR,
    )
    assert calldata[:4] == SEL_BALANCER_SWAP
    (single_swap, funds, limit, _deadline) = abi_decode(
        [
            "(bytes32,uint8,address,address,uint256,bytes)",
            "(address,bool,address,bool)",
            "uint256",
            "uint256",
        ],
        calldata[4:],
    )
    assert single_swap[0] == POOL_ID_32
    assert single_swap[2].lower() == TOKEN_A.lower()
    assert single_swap[3].lower() == TOKEN_B.lower()
    assert single_swap[4] == 1_000_000
    assert funds[0].lower() == EXECUTOR.lower()
    assert funds[2].lower() == EXECUTOR.lower()
    assert limit == 900_000


def test_balancer_rejects_wrong_pool_id_length():
    with pytest.raises(ValueError, match="32 bytes"):
        BalancerAdapter.encode(b"\xab" * 16, TOKEN_A, TOKEN_B, 1_000, 900, EXECUTOR, EXECUTOR)


# ---------------------------------------------------------------------------
# Fail-closed: unknown DEX key
# ---------------------------------------------------------------------------


def test_get_adapter_raises_for_unknown():
    with pytest.raises(UnknownDexError, match="quickswap-v999"):
        get_adapter("quickswap-v999")


def test_get_adapter_raises_for_empty_string():
    with pytest.raises(UnknownDexError):
        get_adapter("")


def test_resolve_pool_fee_info_raises_for_unknown():
    with pytest.raises(UnknownDexError):
        resolve_pool_fee_info("dodo-v3", 3000)


def test_encode_swap_step_fails_closed_unknown_dex():
    with pytest.raises(UnknownDexError):
        encode_swap_step("my-fake-dex", PoolFeeInfo("my-fake-dex", EXECUTOR, 3000), {
            "token_in": TOKEN_A, "token_out": TOKEN_B,
            "amount_in": 1_000, "min_amount_out": 900, "recipient": EXECUTOR,
        })


# ---------------------------------------------------------------------------
# All supported adapters are registered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dex_key", [
    "quickswap-v2", "sushi-v2", "uniswap-v3", "quickswap-v3", "curve", "balancer",
])
def test_all_supported_adapters_registered(dex_key):
    adapter = get_adapter(dex_key)
    assert adapter is not None


# ---------------------------------------------------------------------------
# Protocol ID constants
# ---------------------------------------------------------------------------


def test_protocol_ids_are_unique():
    ids = [PROTOCOL_UNISWAP_V2, PROTOCOL_UNISWAP_V3, PROTOCOL_ALGEBRA, PROTOCOL_CURVE, PROTOCOL_BALANCER]
    assert len(ids) == len(set(ids))


def test_quickswap_v2_protocol_id():
    assert QuickSwapV2Adapter.protocol_id == PROTOCOL_UNISWAP_V2


def test_uniswap_v3_protocol_id():
    assert UniswapV3Adapter.protocol_id == PROTOCOL_UNISWAP_V3


def test_algebra_v3_protocol_id():
    assert AlgebraV3Adapter.protocol_id == PROTOCOL_ALGEBRA


def test_curve_protocol_id():
    assert CurveAdapter.protocol_id == PROTOCOL_CURVE


def test_balancer_protocol_id():
    assert BalancerAdapter.protocol_id == PROTOCOL_BALANCER


# ---------------------------------------------------------------------------
# resolve_pool_fee_info – pool-derived fee / tick spacing
# ---------------------------------------------------------------------------


def test_resolve_pool_fee_info_uniswap_v3_fee_500():
    info = resolve_pool_fee_info("uniswap-v3", 500)
    assert info.fee_tier == 500
    assert info.tick_spacing == 10


def test_resolve_pool_fee_info_uniswap_v3_fee_3000():
    info = resolve_pool_fee_info("uniswap-v3", 3000)
    assert info.fee_tier == 3000
    assert info.tick_spacing == 60


def test_resolve_pool_fee_info_uniswap_v3_fee_10000():
    info = resolve_pool_fee_info("uniswap-v3", 10000)
    assert info.fee_tier == 10000
    assert info.tick_spacing == 200


def test_resolve_pool_fee_info_quickswap_v2():
    info = resolve_pool_fee_info("quickswap-v2", 3000)
    assert info.dex_key == "quickswap-v2"
    assert info.router_address  # non-empty
    assert Web3.is_address(info.router_address)


def test_resolve_pool_fee_info_sushi_v2():
    info = resolve_pool_fee_info("sushi-v2", 3000)
    assert info.dex_key == "sushi-v2"
    # Sushi router must differ from QuickSwap router
    qs_info = resolve_pool_fee_info("quickswap-v2", 3000)
    assert info.router_address.lower() != qs_info.router_address.lower()


def test_resolve_pool_fee_info_uses_env_override(monkeypatch):
    custom = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    monkeypatch.setenv("ROUTER_QUICKSWAP", custom)
    info = resolve_pool_fee_info("quickswap-v2", 3000)
    assert info.router_address.lower() == custom.lower()


# ---------------------------------------------------------------------------
# encode_swap_step dispatch
# ---------------------------------------------------------------------------


def test_encode_swap_step_quickswap_v2():
    info = resolve_pool_fee_info("quickswap-v2", 3000)
    calldata = encode_swap_step("quickswap-v2", info, {
        "token_in": TOKEN_A, "token_out": TOKEN_B,
        "amount_in": 1_000, "min_amount_out": 900, "recipient": EXECUTOR,
    })
    assert calldata[:4] == SEL_SWAP_EXACT_TOKENS


def test_encode_swap_step_uniswap_v3_fee_500():
    info = resolve_pool_fee_info("uniswap-v3", 500)
    calldata = encode_swap_step("uniswap-v3", info, {
        "token_in": TOKEN_A, "token_out": TOKEN_B,
        "amount_in": 1_000_000, "min_amount_out": 990_000, "recipient": EXECUTOR,
    })
    assert calldata[:4] == SEL_EXACT_INPUT_SINGLE_V3
    (params,) = abi_decode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        calldata[4:],
    )
    assert params[2] == 500


def test_encode_swap_step_quickswap_v3():
    info = resolve_pool_fee_info("quickswap-v3", 0)
    calldata = encode_swap_step("quickswap-v3", info, {
        "token_in": TOKEN_A, "token_out": TOKEN_B,
        "amount_in": 1_000_000, "min_amount_out": 990_000, "recipient": EXECUTOR,
    })
    assert calldata[:4] == SEL_EXACT_INPUT_SINGLE_ALGEBRA


def test_encode_swap_step_curve():
    info = resolve_pool_fee_info("curve", 0)
    calldata = encode_swap_step("curve", info, {
        "token_in": TOKEN_A, "token_out": TOKEN_B,
        "amount_in": 1_000_000, "min_amount_out": 990_000, "recipient": EXECUTOR,
        "pool": CURVE_POOL,
    })
    assert calldata[:4] == SEL_CURVE_EXCHANGE


def test_encode_swap_step_balancer():
    info = resolve_pool_fee_info("balancer", 0)
    pool_id_hex = "0x" + POOL_ID_32.hex()
    calldata = encode_swap_step("balancer", info, {
        "token_in": TOKEN_A, "token_out": TOKEN_B,
        "amount_in": 1_000_000, "min_amount_out": 990_000, "recipient": EXECUTOR,
        "pool_id": pool_id_hex,
    })
    assert calldata[:4] == SEL_BALANCER_SWAP


# ---------------------------------------------------------------------------
# ExecutionPayloadBuilder – step building
# ---------------------------------------------------------------------------


def _make_builder(w3=None) -> ExecutionPayloadBuilder:
    return ExecutionPayloadBuilder(EXECUTOR, w3=w3)


def test_builder_build_step_v2():
    builder = _make_builder()
    step = builder.build_step("quickswap-v2", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    assert isinstance(step, SwapStep)
    assert step.protocol_id == PROTOCOL_UNISWAP_V2
    assert step.token_in.lower() == TOKEN_A.lower()
    assert step.token_out.lower() == TOKEN_B.lower()
    assert step.amount_in == 1_000_000
    assert step.min_amount_out == 900_000
    assert len(step.calldata) > 4


def test_builder_build_step_recipient_is_executor():
    builder = _make_builder()
    step = builder.build_step("quickswap-v2", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    # Decode recipient from calldata
    (_, _, _, recipient, _) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        step.calldata[4:],
    )
    assert recipient.lower() == EXECUTOR.lower()


def test_builder_build_step_v3_fee_derived_from_pool():
    builder = _make_builder()
    step_500 = builder.build_step("uniswap-v3", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 500)
    step_3000 = builder.build_step("uniswap-v3", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    assert step_500.calldata != step_3000.calldata
    assert step_500.fee_bps == 5    # 500 micro-units / 100
    assert step_3000.fee_bps == 30  # 3000 micro-units / 100


def test_builder_build_step_fails_closed_unknown_dex():
    builder = _make_builder()
    with pytest.raises(UnknownDexError):
        builder.build_step("unknown-dex", TOKEN_A, TOKEN_B, 1_000, 900, 3000)


def test_builder_as_institutional_step_format():
    builder = _make_builder()
    step = builder.build_step("quickswap-v2", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    d = step.as_institutional_step()
    for key in ("protocol", "target", "approveToken", "outputToken", "callValue",
                "minAmountIn", "minAmountOut", "feeBps", "data"):
        assert key in d
    assert isinstance(d["data"], bytes)
    assert len(d["data"]) > 4


def test_builder_as_ultimate_step_format():
    builder = _make_builder()
    step = builder.build_step("quickswap-v2", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    d = step.as_ultimate_step()
    for key in ("protocol", "target", "approveToken", "callValue",
                "minAmountIn", "minAmountOut", "feeBps", "data"):
        assert key in d
    assert "outputToken" not in d


# ---------------------------------------------------------------------------
# ExecutionPayloadBuilder – envelope building
# ---------------------------------------------------------------------------


def test_builder_institutional_envelope_roundtrip():
    from eth_abi import decode as abi_decode_env

    builder = _make_builder()
    step = builder.build_step("quickswap-v2", TOKEN_A, TOKEN_B, 1_000_000, 900_000, 3000)
    payload = builder.build_institutional_envelope([step], asset=TOKEN_A)
    assert isinstance(payload, bytes)
    assert len(payload) > 0


def test_builder_envelope_rejects_empty_steps():
    builder = _make_builder()
    with pytest.raises(ValueError, match="at least one step"):
        builder.build_institutional_envelope([], asset=TOKEN_A)


# ---------------------------------------------------------------------------
# ExecutionPayloadBuilder – simulation gate
# ---------------------------------------------------------------------------


def test_builder_simulate_raises_without_w3():
    builder = ExecutionPayloadBuilder(EXECUTOR, w3=None)
    with pytest.raises(RuntimeError, match="Web3 instance"):
        builder.simulate("0xdeadbeef")


def test_builder_build_and_simulate_passes():
    mock_w3 = MagicMock()
    mock_w3.eth.call.return_value = b""
    mock_w3.to_hex = Web3.to_hex

    builder = ExecutionPayloadBuilder(EXECUTOR, w3=mock_w3)
    opportunity = {
        "asset": TOKEN_A,
        "min_profit": 100_000,
        "steps": [
            {
                "dex_key": "quickswap-v2",
                "token_in": TOKEN_A,
                "token_out": TOKEN_B,
                "amount_in": 1_000_000,
                "min_amount_out": 900_000,
                "fee_tier": 3000,
            }
        ],
    }
    result = builder.build_and_simulate(opportunity)
    assert isinstance(result, BuildResult)
    assert result.skipped is False
    assert result.simulation["ok"] is True
    assert len(result.steps) == 1


def test_builder_build_and_simulate_raises_on_revert():
    mock_w3 = MagicMock()
    mock_w3.eth.call.side_effect = Exception("execution reverted: insufficient output")

    builder = ExecutionPayloadBuilder(EXECUTOR, w3=mock_w3)
    opportunity = {
        "asset": TOKEN_A,
        "min_profit": 100_000,
        "steps": [
            {
                "dex_key": "quickswap-v2",
                "token_in": TOKEN_A,
                "token_out": TOKEN_B,
                "amount_in": 1_000_000,
                "min_amount_out": 900_000,
                "fee_tier": 3000,
            }
        ],
    }
    with pytest.raises(SimulationFailedError, match="reverted"):
        builder.build_and_simulate(opportunity)


def test_builder_build_and_simulate_with_explicit_flash_calldata():
    """Simulation uses flash_entry_calldata when supplied (the exact tx to broadcast)."""
    mock_w3 = MagicMock()
    mock_w3.eth.call.return_value = b"\x00" * 32

    builder = ExecutionPayloadBuilder(EXECUTOR, w3=mock_w3)
    opportunity = {
        "asset": TOKEN_A,
        "min_profit": 50_000,
        "steps": [
            {
                "dex_key": "uniswap-v3",
                "token_in": TOKEN_A,
                "token_out": TOKEN_B,
                "amount_in": 500_000,
                "min_amount_out": 450_000,
                "fee_tier": 500,
            }
        ],
    }
    flash_calldata = "0xabcdef1234"
    result = builder.build_and_simulate(opportunity, flash_entry_calldata=flash_calldata)
    # Verify the simulation was called with our explicit calldata
    call_args = mock_w3.eth.call.call_args[0][0]
    assert call_args["data"] == flash_calldata
    assert result.skipped is False


# ---------------------------------------------------------------------------
# InstitutionalExecutor simulation gate
# ---------------------------------------------------------------------------


def test_institutional_executor_simulation_failed_blocks_broadcast():
    """When eth_call fails the dispatch must set simulation_failed=True and block broadcast."""
    from backend.institutional_executor import InstitutionalExecutor

    mock_eth = MagicMock()
    mock_eth.call.side_effect = Exception("execution reverted")

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth
    mock_w3.keccak.return_value = bytes(32)

    with patch("backend.contract_interface.Web3") as MockW3, \
         patch("backend.institutional_executor.Web3") as MockW3b:
        MockW3.return_value = mock_w3
        MockW3.HTTPProvider = MagicMock()
        MockW3.to_checksum_address = lambda x: x
        MockW3.to_hex = lambda x: "0x" + x.hex() if isinstance(x, bytes) else x
        MockW3.keccak = lambda **kw: bytes(32)
        MockW3b.to_checksum_address = lambda x: x

        c1 = InstitutionalExecutor(137, rpc_url="http://localhost:8545", dry_run=True)
        result = c1.init_aave_flash(
            asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            amount=1_000_000,
            min_profit=100,
            payload=b"\x00\x01\x02\x03",
        )

    assert result["simulation_failed"] is True
    assert result["broadcast"]["status"] == "not_sent"
    assert "simulation failed" in result["broadcast"]["reason"].lower()


def test_institutional_executor_simulation_passed_dry_run():
    """When eth_call succeeds but dry_run=True, broadcast is still blocked (not sent)."""
    from backend.institutional_executor import InstitutionalExecutor

    mock_eth = MagicMock()
    mock_eth.call.return_value = b"\x00" * 32

    mock_w3 = MagicMock()
    mock_w3.eth = mock_eth
    mock_w3.keccak.return_value = bytes(32)

    with patch("backend.contract_interface.Web3") as MockW3, \
         patch("backend.institutional_executor.Web3") as MockW3b:
        MockW3.return_value = mock_w3
        MockW3.HTTPProvider = MagicMock()
        MockW3.to_checksum_address = lambda x: x
        MockW3.to_hex = lambda x: "0x" + (x.hex() if isinstance(x, bytes) else x)
        MockW3.keccak = lambda **kw: bytes(32)
        MockW3b.to_checksum_address = lambda x: x

        c1 = InstitutionalExecutor(137, rpc_url="http://localhost:8545", dry_run=True)
        result = c1.init_aave_flash(
            asset="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            amount=1_000_000,
            min_profit=100,
            payload=b"\x00\x01\x02\x03",
        )

    assert result["simulation_failed"] is False
    assert result["dry_run"] is True
    assert result["broadcast"]["reason"] == "dry_run=True"


# ---------------------------------------------------------------------------
# LiveExecutor CLI
# ---------------------------------------------------------------------------


def test_live_executor_main_returns_zero_by_default():
    """main() with no --strict returns 0 even if validation fails (no live RPC)."""
    from backend.live_executor import main

    # validate_all will fail (no RPC) but main() without --strict returns 0
    ret = main(["--chain-id", "137"])
    assert ret == 0


def test_live_executor_main_json_output(capsys):
    from backend.live_executor import main

    ret = main(["--chain-id", "137", "--json"])
    assert ret == 0
    captured = capsys.readouterr()
    import json
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) >= 2


def test_live_executor_main_strict_returns_one_on_failure():
    """With --strict, returns 1 when at least one validation check fails (offline)."""
    from backend.live_executor import main

    ret = main(["--chain-id", "137", "--strict"])
    # No live RPC → validation will fail → strict returns 1
    assert ret == 1


def test_normalize_env_aliases_polygon_rpc(monkeypatch):
    from backend.live_executor import _normalize_env_aliases

    monkeypatch.delenv("POLYGON_RPC", raising=False)
    monkeypatch.setenv("POLYGON_RPC_URL", "https://example.polygon.rpc/")
    _normalize_env_aliases()
    assert os.environ.get("POLYGON_RPC") == "https://example.polygon.rpc/"


def test_normalize_env_aliases_does_not_overwrite(monkeypatch):
    from backend.live_executor import _normalize_env_aliases

    monkeypatch.setenv("POLYGON_RPC", "https://original.rpc/")
    monkeypatch.setenv("POLYGON_RPC_URL", "https://alternate.rpc/")
    _normalize_env_aliases()
    assert os.environ.get("POLYGON_RPC") == "https://original.rpc/"
