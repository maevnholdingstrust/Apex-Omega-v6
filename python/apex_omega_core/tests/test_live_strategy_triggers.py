import pytest

from apex_omega_core.strategies.live_strategy_triggers import C2TriggerContext, RawStrategyCall

ADDR = "0x0000000000000000000000000000000000000001"


def test_c2_context_requires_post_c1_block():
    ctx = C2TriggerContext(parent_redis_id="p1", c1_tx_hash="0x" + "11" * 32, c1_block=100, current_block=101)
    ctx.validate()


def test_c2_context_rejects_same_block():
    ctx = C2TriggerContext(parent_redis_id="p1", c1_tx_hash="0x" + "11" * 32, c1_block=100, current_block=100)
    with pytest.raises(ValueError):
        ctx.validate()


def test_c2_context_rejects_expired_window():
    ctx = C2TriggerContext(parent_redis_id="p1", c1_tx_hash="0x" + "11" * 32, c1_block=100, current_block=106, max_delay_blocks=5)
    with pytest.raises(ValueError):
        ctx.validate()


def test_raw_strategy_call_requires_hex_calldata():
    call = RawStrategyCall(target=ADDR, calldata="0x1234", p_net_usd=5.0, context={})
    call.validate()


def test_raw_strategy_call_rejects_empty_calldata():
    call = RawStrategyCall(target=ADDR, calldata="0x", p_net_usd=5.0, context={})
    with pytest.raises(ValueError):
        call.validate()
