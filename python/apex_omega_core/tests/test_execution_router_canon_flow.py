import asyncio
from unittest.mock import AsyncMock, Mock

from apex_omega_core.strategies.execution_router import ExecutionRouter


def test_c2_not_called_before_c1_fork_sim_and_execute_and_reload() -> None:
    router = ExecutionRouter()
    order = []

    router.strategies['aggressor'].prepare_contract_strike = Mock(return_value={
        'action': 'EXECUTE',
        'sentinel_output': {'optimal_input': 1000.0, 'profit': 10.0},
    })
    router.strategies['aggressor'].execute_contract_strike = AsyncMock(return_value={'success': True})

    def fake_fork(leg, plan, route):
        order.append(f"fork_{leg}")
        return {'status': 'PASS', 'leg': leg}

    def fake_reload(route, c1, c1_exec):
        order.append('reload_state')
        return [{'post_c1': True}]

    def fake_c2_decide(route, *_args, **_kwargs):
        assert route == [{'post_c1': True}]
        order.append('c2_decide')
        return {'action': 'NO_OP', 'sentinel_output': {'net_profit_usd': 0.0}}

    router._run_fork_simulation = fake_fork
    router._reload_post_c1_state = fake_reload
    router.strategies['surgeon'].decide_contract_action = fake_c2_decide
    router.strategies['surgeon'].execute_contract_decision = AsyncMock()

    asyncio.run(router.process_discovery_pipeline(route=[{'hop': 1}], raw_spread=1.0))

    assert order == ['fork_C1', 'reload_state', 'c2_decide', 'fork_C2']
    router.strategies['aggressor'].execute_contract_strike.assert_awaited_once()
    router.strategies['surgeon'].execute_contract_decision.assert_not_called()


def test_reload_state_called_between_c1_and_c2() -> None:
    router = ExecutionRouter()
    called = {'reload': False}

    router.strategies['aggressor'].prepare_contract_strike = Mock(return_value={
        'action': 'EXECUTE',
        'sentinel_output': {'optimal_input': 1_500.0, 'profit': 5.0},
    })
    router.strategies['aggressor'].execute_contract_strike = AsyncMock(return_value={'success': True})

    def fake_reload(route, _c1, _exec):
        called['reload'] = True
        return [{'reloaded': True, 'source_len': len(route)}]

    def fake_c2(route, *_args, **_kwargs):
        assert called['reload'] is True
        assert route[0]['reloaded'] is True
        return {'action': 'NO_OP', 'sentinel_output': {'net_profit_usd': 0.0}}

    router._reload_post_c1_state = fake_reload
    router.strategies['surgeon'].decide_contract_action = fake_c2

    result = asyncio.run(router.process_discovery_pipeline(route=[{'x': 1}], raw_spread=2.0))

    assert called['reload'] is True
    assert result['state_reload']['post_c1_route'][0]['reloaded'] is True


def test_c2_output_can_be_no_op_without_failure() -> None:
    router = ExecutionRouter()
    router.strategies['aggressor'].prepare_contract_strike = Mock(return_value={
        'action': 'EXECUTE',
        'sentinel_output': {'optimal_input': 1000.0, 'profit': 1.0},
    })
    router.strategies['aggressor'].execute_contract_strike = AsyncMock(return_value={'success': True})
    router.strategies['surgeon'].decide_contract_action = Mock(return_value={
        'action': 'NO_OP',
        'sentinel_output': {'net_profit_usd': 0.0},
    })
    router.strategies['surgeon'].execute_contract_decision = AsyncMock()

    result = asyncio.run(router.process_discovery_pipeline(route=[{'hop': 1}], raw_spread=0.1))

    assert result['c2']['execution']['decision'] == 'NO_OP'
    assert result['c2']['execution']['executed'] is False
    router.strategies['surgeon'].execute_contract_decision.assert_not_called()


def test_c1_and_c2_fork_simulations_are_separate() -> None:
    router = ExecutionRouter()
    calls = []

    router.strategies['aggressor'].prepare_contract_strike = Mock(return_value={
        'action': 'EXECUTE',
        'sentinel_output': {'optimal_input': 1000.0, 'profit': 1.0},
    })
    router.strategies['aggressor'].execute_contract_strike = AsyncMock(return_value={'success': True})
    router.strategies['surgeon'].decide_contract_action = Mock(return_value={
        'action': 'NO_OP',
        'sentinel_output': {'net_profit_usd': 0.0},
    })

    def fake_fork(leg, plan, route):
        calls.append((leg, len(route)))
        return {'leg': leg, 'status': 'PASS'}

    router._run_fork_simulation = fake_fork

    asyncio.run(router.process_discovery_pipeline(route=[{'a': 1}, {'b': 2}], raw_spread=0.2))

    assert calls[0][0] == 'C1'
    assert calls[1][0] == 'C2'
    assert len(calls) == 2
