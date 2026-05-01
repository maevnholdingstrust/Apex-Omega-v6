from types import SimpleNamespace

from apex_omega_core.execution.pre_execution_pipeline import (
    C2_EXECUTE,
    C2_NO_OP,
    canonical_execution_pipeline,
)


def base_candidate(**overrides):
    data = dict(
        rpc_healthy=True,
        tvl_usd=100_000,
        reserve0=50_000,
        reserve1=50_000,
        reserves_verified=True,
        reserve_staleness_seconds=1,
        pool_type="V2",
        amount_in_usd=1_000,
        weakest_pool_tvl_usd=100_000,
        raw_spread_bps=100,
        expected_profit_usd=20,
        route_calldata=b"1234",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def fork_result(label):
    return SimpleNamespace(
        accepted=True,
        reason=None,
        expected_out=100.0,
        simulated_out=100.0,
        expected_profit=10.0,
        simulated_profit=10.0,
        label=label,
    )


def test_c2_is_not_called_before_c1_fork_sim():
    events = []

    def c1_fn(candidate):
        events.append("c1")
        return SimpleNamespace(stage="c1")

    def c2_fn(post_state):
        events.append("c2")
        return SimpleNamespace(action=C2_EXECUTE)

    def fork_validate(trade):
        events.append(f"fork:{trade.stage}")
        return False, SimpleNamespace(reason="SIM_REVERT")

    result = canonical_execution_pipeline(
        base_candidate(),
        c1_fn,
        c2_fn,
        lambda *_: events.append("execute_c1"),
        lambda *_: events.append("reload_state"),
        fork_validate_fn=fork_validate,
    )

    assert not result.accepted
    assert events == ["c1", "fork:c1"]


def test_c2_is_not_called_before_c1_execution_and_reload():
    events = []

    def c1_fn(candidate):
        events.append("c1")
        return SimpleNamespace(stage="c1")

    def execute_c1(c1_result, c1_fork_result):
        events.append("execute_c1")
        return SimpleNamespace(receipt="0x1")

    def reload_state(candidate, c1_result, c1_execution_result):
        events.append("reload_state")
        return SimpleNamespace(stage="post_c1")

    def c2_fn(post_state):
        events.append("c2")
        return SimpleNamespace(stage="c2", action=C2_NO_OP)

    def fork_validate(trade):
        events.append(f"fork:{trade.stage}")
        return True, fork_result(trade.stage)

    result = canonical_execution_pipeline(
        base_candidate(),
        c1_fn,
        c2_fn,
        execute_c1,
        reload_state,
        fork_validate_fn=fork_validate,
    )

    assert result.accepted
    assert events == ["c1", "fork:c1", "execute_c1", "reload_state", "c2", "fork:c2"]


def test_reload_state_is_called_between_c1_and_c2_and_c2_receives_post_c1_state():
    post_c1_state = SimpleNamespace(stage="post_c1", reserve0=49_000)
    seen_by_c2 = []
    events = []

    def c1_fn(candidate):
        events.append("c1")
        return SimpleNamespace(stage="c1")

    def reload_state(*_):
        events.append("reload_state")
        return post_c1_state

    def c2_fn(state):
        events.append("c2")
        seen_by_c2.append(state)
        return SimpleNamespace(stage="c2", action=C2_EXECUTE)

    def fork_validate(trade):
        events.append(f"fork:{trade.stage}")
        return True, fork_result(trade.stage)

    result = canonical_execution_pipeline(
        base_candidate(),
        c1_fn,
        c2_fn,
        lambda *_: events.append("execute_c1"),
        reload_state,
        execute_c2_fn=lambda *_: events.append("execute_c2"),
        fork_validate_fn=fork_validate,
    )

    assert result.accepted
    assert events == ["c1", "fork:c1", "execute_c1", "reload_state", "c2", "fork:c2", "execute_c2"]
    assert seen_by_c2 == [post_c1_state]
    assert result.post_c1_state is post_c1_state


def test_c2_can_return_no_op_without_failure():
    result = canonical_execution_pipeline(
        base_candidate(),
        lambda _: SimpleNamespace(stage="c1"),
        lambda _: SimpleNamespace(stage="c2", action=C2_NO_OP),
        lambda *_: SimpleNamespace(receipt="0x1"),
        lambda *_: SimpleNamespace(stage="post_c1"),
        fork_validate_fn=lambda trade: (True, fork_result(trade.stage)),
    )

    assert result.accepted
    assert result.reason == C2_NO_OP
    assert result.c2_action == C2_NO_OP


def test_c2_rejects_non_canonical_output_actions():
    result = canonical_execution_pipeline(
        base_candidate(),
        lambda _: SimpleNamespace(stage="c1"),
        lambda _: SimpleNamespace(stage="c2", action="STRIKE"),
        lambda *_: SimpleNamespace(receipt="0x1"),
        lambda *_: SimpleNamespace(stage="post_c1"),
        fork_validate_fn=lambda trade: (True, fork_result(trade.stage)),
    )

    assert not result.accepted
    assert result.reason == "INVALID_C2_ACTION"
    assert result.c2_action == "STRIKE"
    assert result.c2_fork_result is None


def test_c1_fork_sim_and_c2_fork_sim_are_separate_validations():
    validated = []

    def fork_validate(trade):
        validated.append(trade.stage)
        return True, fork_result(trade.stage)

    result = canonical_execution_pipeline(
        base_candidate(),
        lambda _: SimpleNamespace(stage="c1"),
        lambda _: SimpleNamespace(stage="c2", action=C2_EXECUTE),
        lambda *_: SimpleNamespace(receipt="0x1"),
        lambda *_: SimpleNamespace(stage="post_c1"),
        fork_validate_fn=fork_validate,
    )

    assert result.accepted
    assert validated == ["c1", "c2"]
    assert result.c1_fork_result.label == "c1"
    assert result.c2_fork_result.label == "c2"
