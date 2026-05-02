import os


class DryRunBroadcastBlockedError(RuntimeError):
    pass


_REQUIRED_ENV = {
    'LIVE_EXECUTION': 'false',
    'EXECUTION_ENABLED': 'false',
    'BROADCAST_ENABLED': 'false',
    'DRY_RUN_DASHBOARD_MODE': 'true',
    'TX_SIGNING_ENABLED': 'false',
    'PRIVATE_RELAY_ENABLED': 'false',
    'TITAN_RELAY_ENABLED': 'false',
    'FLASHBOTS_RELAY_ENABLED': 'false',
}


def enforce_no_broadcast_env() -> None:
    for k, v in _REQUIRED_ENV.items():
        os.environ[k] = v


def assert_dry_run_env() -> None:
    for k, v in _REQUIRED_ENV.items():
        if os.getenv(k, '').lower() != v:
            raise DryRunBroadcastBlockedError(f"Dry-run env violation: {k} must be {v}")


def assert_no_broadcast(action_name: str) -> None:
    assert_dry_run_env()
    blocked = ['eth_sendRawTransaction', 'eth_sendTransaction', 'broadcast', 'relay submit']
    if any(x.lower() in action_name.lower() for x in blocked):
        raise DryRunBroadcastBlockedError(f"Blocked broadcast action: {action_name}")


def assert_no_signing(action_name: str) -> None:
    assert_dry_run_env()
    if 'sign' in action_name.lower() or 'wallet' in action_name.lower():
        raise DryRunBroadcastBlockedError(f"Blocked signing action: {action_name}")


def assert_no_relay_submission(action_name: str) -> None:
    assert_dry_run_env()
    if any(x in action_name.lower() for x in ['relay', 'flashbots', 'titan', 'bundle']):
        raise DryRunBroadcastBlockedError(f"Blocked relay action: {action_name}")
