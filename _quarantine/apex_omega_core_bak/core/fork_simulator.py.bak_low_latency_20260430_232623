from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForkSimResult:
    success: bool
    expected_profit: float
    error: str | None


def simulate_on_fork(calldata: bytes, expected_profit: float) -> ForkSimResult:
    # Placeholder: requires local fork RPC (e.g. anvil / hardhat) wired via .env
    if not calldata:
        return ForkSimResult(False, 0.0, "empty calldata")
    # Fail-closed until fork infra is configured
    return ForkSimResult(False, expected_profit, "fork simulation not configured")
