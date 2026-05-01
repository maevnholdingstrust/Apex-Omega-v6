"""
Dry-Run Safety Guard Module

Implements hard no-broadcast safety for dry-run mode.
Blocks all live execution paths: signing, broadcasting, relay submission.

Classes:
    DryRunBroadcastBlockedError: Raised when broadcast attempt detected

Functions:
    assert_dry_run_env(): Assert dry-run environment is active
    assert_no_broadcast(action_name: str): Block broadcast attempts
    assert_no_signing(action_name: str): Block signing attempts
    assert_no_relay_submission(action_name: str): Block relay submission
    enforce_no_broadcast_env(): Force dry-run defaults
    is_dry_run_mode(): Check if dry-run mode is active
"""

import os
from typing import Optional


class DryRunBroadcastBlockedError(Exception):
    """Raised when a broadcast/signing attempt is made in dry-run mode."""
    
    def __init__(self, action_name: str, details: Optional[str] = None):
        self.action_name = action_name
        self.details = details
        msg = f"BROADCAST BLOCKED in dry-run mode: {action_name}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


# Environment variable names for dry-run safety
DRY_RUN_ENV_VARS = {
    "LIVE_EXECUTION": "false",
    "EXECUTION_ENABLED": "false",
    "BROADCAST_ENABLED": "false",
    "DRY_RUN_DASHBOARD_MODE": "true",
    "TX_SIGNING_ENABLED": "false",
    "PRIVATE_RELAY_ENABLED": "false",
    "TITAN_RELAY_ENABLED": "false",
    "FLASHBOTS_RELAY_ENABLED": "false",
}

# Blocked action patterns
BLOCKED_ACTIONS = {
    "eth_sendRawTransaction",
    "eth_sendTransaction",
    "relay_bundle_submit",
    "private_relay_submit",
    "titan_relay_submit",
    "flashbots_relay_submit",
    "real_wallet_signing",
    "state_changing_broadcast",
    "contract_write",
    "nonce_submit",
}


def is_dry_run_mode() -> bool:
    """
    Check if dry-run mode is active.
    
    Returns:
        True if dry-run mode is enforced, False otherwise.
    """
    return os.environ.get("DRY_RUN_DASHBOARD_MODE", "").lower() == "true"


def enforce_no_broadcast_env() -> None:
    """
    Force dry-run environment defaults.
    
    Sets all required environment variables to safe dry-run values.
    """
    for key, value in DRY_RUN_ENV_VARS.items():
        os.environ[key] = value


def assert_dry_run_env() -> None:
    """
    Assert that dry-run environment is active.
    
    Raises:
        DryRunBroadcastBlockedError: If dry-run mode is not active.
    """
    if not is_dry_run_mode():
        raise DryRunBroadcastBlockedError(
            "assert_dry_run_env",
            "Dry-run environment not active. Set DRY_RUN_DASHBOARD_MODE=true"
        )


def assert_no_broadcast(action_name: str) -> None:
    """
    Block any broadcast attempt.
    
    Args:
        action_name: Name of the attempted broadcast action.
        
    Raises:
        DryRunBroadcastBlockedError: Always in dry-run mode.
    """
    if is_dry_run_mode():
        raise DryRunBroadcastBlockedError(
            action_name,
            "Broadcast attempted in dry-run mode. No transactions will be sent."
        )


def assert_no_signing(action_name: str) -> None:
    """
    Block any signing attempt.
    
    Args:
        action_name: Name of the attempted signing action.
        
    Raises:
        DryRunBroadcastBlockedError: Always in dry-run mode.
    """
    if is_dry_run_mode():
        raise DryRunBroadcastBlockedError(
            action_name,
            "Signing attempted in dry-run mode. Transactions will not be signed."
        )


def assert_no_relay_submission(action_name: str) -> None:
    """
    Block any relay submission attempt.
    
    Args:
        action_name: Name of the attempted relay action.
        
    Raises:
        DryRunBroadcastBlockedError: Always in dry-run mode.
    """
    if is_dry_run_mode():
        raise DryRunBroadcastBlockedError(
            action_name,
            "Relay submission attempted in dry-run mode. No bundles will be submitted."
        )


def get_dry_run_env() -> dict:
    """
    Get current dry-run environment configuration.
    
    Returns:
        Dictionary of environment variable names and their values.
    """
    return {
        key: os.environ.get(key, "not_set")
        for key in DRY_RUN_ENV_VARS.keys()
    }


def validate_dry_run_safety() -> tuple[bool, list[str]]:
    """
    Validate that all dry-run safety guards are properly configured.
    
    Returns:
        Tuple of (is_safe, issues) where issues is a list of problems found.
    """
    issues = []
    
    # Check all required env vars
    for key, expected in DRY_RUN_ENV_VARS.items():
        actual = os.environ.get(key, "")
        if actual.lower() != expected.lower():
            issues.append(f"{key}={actual} (expected {expected})")
    
    # Check for disabled flags
    if os.environ.get("LIVE_EXECUTION", "").lower() == "true":
        issues.append("LIVE_EXECUTION is enabled - unsafe for dry-run")
    
    if os.environ.get("BROADCAST_ENABLED", "").lower() == "true":
        issues.append("BROADCAST_ENABLED is enabled - unsafe for dry-run")
    
    is_safe = len(issues) == 0
    return is_safe, issues