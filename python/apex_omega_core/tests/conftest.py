import pytest
from apex_omega_core.core.types import Spread


@pytest.fixture
def sample_spread():
    return Spread(symbol='EURUSD', bid=1.1000, ask=1.1005, timestamp=1234567890.0)


# ---------------------------------------------------------------------------
# Live RPC fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def live_pool_state():
    """Return a two-leg pool state dict fetched from the live Polygon RPC.

    The dict contains the canonical keys consumed by
    ``SSOTPipelineFinalizer.run()`` and ``BatchSimulator.run()``:

        fee1, r1_in, r1_out, fee2, r2_in, r2_out, c_total

    If the RPC endpoint is unreachable this fixture skips the calling test
    with a clear message so that CI without network access is unaffected.
    Mark any test that uses this fixture with ``@pytest.mark.live`` so it
    can be excluded with ``-m 'not live'`` when desired.
    """
    from apex_omega_core.core import rpc_tester

    if not rpc_tester.is_live_available():
        pytest.skip(
            f"Live Polygon RPC unreachable at {rpc_tester.RPC_URL!r}. "
            "Set POLYGON_RPC / POLYGON_HTTP in the environment to enable "
            "live tests, or run with -m 'not live' to skip them."
        )

    try:
        return rpc_tester.get_canonical_two_leg_state()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not fetch live pool state: {exc}")