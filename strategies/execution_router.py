from strategies.c1_aggressor_apex import C1AggressorApex
from strategies.c2_surgeon_apex import C2SurgeonApex
from core.types import ExecutionResult

class ExecutionRouter:
    """Smart decision engine for execution strategies."""

    def __init__(self):
        self.strategies = {
            'aggressor': C1AggressorApex(),
            'surgeon': C2SurgeonApex()
        }

    def route(self, order: dict, strategy: str) -> ExecutionResult:
        """Route to appropriate strategy."""
        if strategy in self.strategies:
            return self.strategies[strategy].execute(order)
        return ExecutionResult(success=False)