"""Apex-Omega v6 backend package.

Provides the canonical executor registry, contract interface layer,
strategy-specific executor wrappers, live execution orchestrator, and
server API.  All contract addresses and ABIs are sourced from
:mod:`backend.executor_registry`; no module in this package hard-codes
addresses or function signatures independently.
"""
