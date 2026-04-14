import pytest
from core.inference import derive_net_edge
from strategies.execution_router import ExecutionRouter

def test_integration():
    # Test end-to-end
    data = {'edge': 0.05}
    result = derive_net_edge(data)
    assert result.net_edge == 0.05

    router = ExecutionRouter()
    exec_result = router.route({'price': 100.0}, 'surgeon')
    assert exec_result.success