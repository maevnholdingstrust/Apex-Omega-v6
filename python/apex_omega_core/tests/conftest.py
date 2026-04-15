import pytest
from apex_omega_core.core.types import Spread

@pytest.fixture
def sample_spread():
    return Spread(symbol='EURUSD', bid=1.1000, ask=1.1005, timestamp=1234567890.0)