"""Live execution orchestrator for Apex-Omega v6.

:class:`LiveExecutor` is the top-level execution entry point that:

1. Consults the executor registry to resolve addresses and ABIs.
2. Runs startup validation before the first execution (configurable).
3. Delegates calldata building to
   :class:`~backend.institutional_executor.InstitutionalExecutor` (C1) or
   :class:`~backend.liquidation_executor_contract.LiquidationExecutorContract`
   (C2).
4. Enforces the ``P_net × P(fill) > 0`` profitability gate.
5. Hands signed transactions off to the MEV relay via
   :class:`~python.apex_omega_core.core.relay_submitter.RelayBundleSubmitter`.

All contract addresses, ABIs, and function signatures are sourced from
:mod:`backend.executor_registry`.

Typical usage
-------------
::

    from backend.live_executor import LiveExecutor

    executor = LiveExecutor(chain_id=137)
    executor.startup_validate()        # check bytecode, selectors, chain ID, owner

    result = executor.execute_c1(strategy_output, p_fill=0.9)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional

from backend.executor_registry import (
    STRATEGY_C1,
    STRATEGY_C2,
    ValidationResult,
    get_entry,
    get_rpc_url,
    validate_all,
    validate_registry_entry,
)
from backend.institutional_executor import InstitutionalExecutor
from backend.liquidation_executor_contract import LiquidationExecutorContract

logger = logging.getLogger(__name__)

_TRUE = frozenset({"1", "true", "yes", "y", "on"})


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "")
    return v.strip().lower() in _TRUE if v else default


class LiveExecutor:
    """Registry-driven live execution orchestrator.

    Parameters
    ----------
    chain_id:
        EIP-155 chain ID.  Defaults to ``137`` (Polygon).
    rpc_url:
        Override RPC URL.  Resolved from the registry when absent.
    validate_on_init:
        When ``True``, :meth:`startup_validate` is called in the
        constructor.  Defaults to ``False`` to allow lazy initialisation.
    """

    def __init__(
        self,
        chain_id: int = 137,
        *,
        rpc_url: Optional[str] = None,
        validate_on_init: bool = False,
    ):
        self.chain_id = chain_id
        self._rpc_url = rpc_url or get_rpc_url(chain_id)

        # Execution mode flags – honour the existing env var contract
        self._live_trading_enabled = _env_bool("LIVE_TRADING_ENABLED", False)
        self._dry_run = _env_bool("DRY_RUN", True)

        dry_run = not self._live_trading_enabled or self._dry_run

        self._c1 = InstitutionalExecutor(
            chain_id, rpc_url=self._rpc_url, dry_run=dry_run
        )
        self._c2 = LiquidationExecutorContract(
            chain_id, rpc_url=self._rpc_url, dry_run=dry_run
        )

        if validate_on_init:
            self.startup_validate()

    # ── startup validation ────────────────────────────────────────────────

    def startup_validate(self) -> List[ValidationResult]:
        """Validate all registry entries on this chain.

        Logs a summary for each entry.  Returns the list of
        :class:`~backend.executor_registry.ValidationResult` objects so
        the caller can inspect individual checks.
        """
        logger.info(
            "Running executor registry startup validation for chain_id=%d…",
            self.chain_id,
        )
        results = validate_all(chain_id=self.chain_id, rpc_url=self._rpc_url)
        failed = [r for r in results if not r.passed]
        if failed:
            logger.warning(
                "%d/%d registry validation check(s) failed on chain_id=%d.",
                len(failed),
                len(results),
                self.chain_id,
            )
        else:
            logger.info(
                "All %d registry validation check(s) passed on chain_id=%d.",
                len(results),
                self.chain_id,
            )
        return results

    def validate_c1(self) -> ValidationResult:
        """Validate the C1 registry entry only."""
        return validate_registry_entry(
            get_entry(self.chain_id, STRATEGY_C1), rpc_url=self._rpc_url
        )

    def validate_c2(self) -> ValidationResult:
        """Validate the C2 registry entry only."""
        return validate_registry_entry(
            get_entry(self.chain_id, STRATEGY_C2), rpc_url=self._rpc_url
        )

    # ── execution ─────────────────────────────────────────────────────────

    def execute_c1(
        self,
        strategy_output: Mapping[str, Any],
        *,
        p_fill: float = 1.0,
        flash_loan_provider: str = "aave_v3",
    ) -> Dict[str, Any]:
        """Execute a C1 (InstitutionalExecutor) arbitrage trade.

        Parameters
        ----------
        strategy_output:
            Dict produced by the C1 strategy pipeline.  Expected keys:
            ``asset``, ``flash_loan_amount``, ``min_profit``, ``payload``
            (ABI-encoded route envelope bytes), and optionally
            ``net_profit_usd``.
        p_fill:
            Estimated probability of inclusion in the next block
            (0.0 – 1.0).  Used to evaluate the
            ``P_net × P(fill) > 0`` profitability gate.
        flash_loan_provider:
            ``"aave_v3"`` (default) or ``"balancer"`` / ``"balancer_v3"``.

        Returns
        -------
        dict
            Execution result dict.  ``"skipped"`` is ``True`` when the
            profitability gate rejects the trade.
        """
        net_profit_usd = float(strategy_output.get("net_profit_usd", 0.0))
        if not self._profitability_gate(net_profit_usd, p_fill):
            return {
                "skipped": True,
                "reason": f"Profitability gate: net_profit_usd={net_profit_usd:.4f} * p_fill={p_fill:.4f} <= 0",
                "strategy": STRATEGY_C1,
            }

        asset = str(strategy_output["asset"])
        amount = int(strategy_output["flash_loan_amount"])
        min_profit = int(strategy_output["min_profit"])
        payload = bytes(strategy_output.get("payload", b""))

        provider = flash_loan_provider.lower()
        if provider in {"balancer", "balancer_v3"}:
            result = self._c1.init_balancer_flash(asset, amount, min_profit, payload)
        else:
            result = self._c1.init_aave_flash(asset, amount, min_profit, payload)

        result["strategy"] = STRATEGY_C1
        result["net_profit_usd"] = net_profit_usd
        result["p_fill"] = p_fill
        result["skipped"] = False
        return result

    def execute_c2(
        self,
        strategy_output: Mapping[str, Any],
        *,
        p_fill: float = 1.0,
        merkle_proof: Optional[List[bytes]] = None,
    ) -> Dict[str, Any]:
        """Execute a C2 (UltimateArbitrageExecutor) arbitrage trade.

        Parameters
        ----------
        strategy_output:
            Dict produced by the C2 strategy pipeline.  Expected keys:
            ``asset``, ``flash_loan_amount``, ``min_profit``, ``payload``,
            and optionally ``net_profit_usd`` and ``merkle_proof``.
        p_fill:
            Estimated probability of block inclusion.
        merkle_proof:
            Explicit Merkle proof list.  Falls back to
            ``strategy_output["merkle_proof"]`` when ``None``.

        Returns
        -------
        dict
            Execution result dict.
        """
        net_profit_usd = float(strategy_output.get("net_profit_usd", 0.0))
        if not self._profitability_gate(net_profit_usd, p_fill):
            return {
                "skipped": True,
                "reason": f"Profitability gate: net_profit_usd={net_profit_usd:.4f} * p_fill={p_fill:.4f} <= 0",
                "strategy": STRATEGY_C2,
            }

        asset = str(strategy_output["asset"])
        amount = int(strategy_output["flash_loan_amount"])
        min_profit = int(strategy_output["min_profit"])
        payload = bytes(strategy_output.get("payload", b""))
        proof = merkle_proof or list(strategy_output.get("merkle_proof", []))

        result = self._c2.execute_arbitrage(asset, amount, min_profit, proof, payload)
        result["strategy"] = STRATEGY_C2
        result["net_profit_usd"] = net_profit_usd
        result["p_fill"] = p_fill
        result["skipped"] = False
        return result

    # ── internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _profitability_gate(net_profit_usd: float, p_fill: float) -> bool:
        """Return ``True`` when both ``P_net > 0`` and ``P(fill) > 0``."""
        return net_profit_usd > 0.0 and p_fill > 0.0

    # ── introspection ─────────────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        """Return ``True`` when live (non-dry-run) execution is enabled."""
        return self._live_trading_enabled and not self._dry_run

    def registry_summary(self) -> List[Dict[str, Any]]:
        """Return serialisable registry entries for this chain."""
        from backend.executor_registry import EXECUTOR_REGISTRY

        return [
            entry.as_dict()
            for (cid, _), entry in EXECUTOR_REGISTRY.items()
            if cid == self.chain_id
        ]

    def __repr__(self) -> str:
        return (
            f"LiveExecutor("
            f"chain={self.chain_id}, "
            f"is_live={self.is_live})"
        )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

#: Environment variable aliases – maps canonical name → list of alternates.
#: When the canonical variable is unset, the first non-empty alternate is used.
_ENV_ALIASES: Dict[str, List[str]] = {
    "POLYGON_RPC":           ["POLYGON_RPC_URL"],
    "ETH_RPC":               ["ETH_RPC_URL"],
    "LIVE_TRADING_ENABLED":  ["LIVE_EXECUTION_ENABLED"],
    "DRY_RUN":               ["DRY_RUN_MODE"],
    "OPERATOR_ADDRESS":      ["EXECUTOR_WALLET", "EXECUTOR_ADDRESS"],
    "C1_INSTITUTIONAL_EXECUTOR_ADDRESS": ["EXECUTOR_C1_ADDRESS", "INSTITUTIONAL_EXECUTOR_ADDRESS"],
    "C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS": ["EXECUTOR_C2_ADDRESS", "ULTIMATE_EXECUTOR_ADDRESS"],
}


def _normalize_env_aliases() -> None:
    """Populate canonical env vars from their accepted aliases.

    Mutates ``os.environ`` in-place so downstream modules see the canonical
    names.  Only sets the canonical variable when it is currently absent.
    """
    for canonical, alternates in _ENV_ALIASES.items():
        if os.getenv(canonical):
            continue
        for alt in alternates:
            value = os.getenv(alt, "").strip()
            if value:
                os.environ[canonical] = value
                logger.debug("env alias: %s → %s (from %s)", alt, canonical, alt)
                break


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for the live executor startup validation.

    Flags
    -----
    ``--chain-id INT``
        EIP-155 chain ID (default: 137 / Polygon).
    ``--rpc-url URL``
        Override RPC URL; otherwise resolved from the environment.
    ``--strict``
        Exit with code 1 when any registry validation check fails.
    ``--json``
        Emit a JSON array of :class:`~backend.executor_registry.ValidationResult`
        dicts to stdout instead of human-readable lines.

    Returns
    -------
    int
        ``0`` on success (or when ``--strict`` is not set), ``1`` when
        ``--strict`` is set and at least one check failed.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="live_executor",
        description="Apex-Omega live executor startup validation",
    )
    parser.add_argument(
        "--chain-id", type=int, default=137,
        metavar="INT",
        help="EIP-155 chain ID (default: 137)",
    )
    parser.add_argument(
        "--rpc-url", default=None,
        metavar="URL",
        help="Override RPC URL (default: resolved from env)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 when any validation check fails",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Emit JSON output instead of human-readable lines",
    )
    args = parser.parse_args(argv)

    # Normalise env aliases before anything else reads them.
    _normalize_env_aliases()

    executor = LiveExecutor(
        chain_id=args.chain_id,
        rpc_url=args.rpc_url,
    )
    results = executor.startup_validate()

    if args.json_output:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] chain={r.chain_id} strategy={r.strategy} address={r.address}")
            for err in r.errors:
                print(f"  ERROR: {err}")
        failed = [r for r in results if not r.passed]
        if failed:
            print(
                f"\n{len(failed)}/{len(results)} validation check(s) failed.",
                file=sys.stderr,
            )
        else:
            print(f"\nAll {len(results)} validation check(s) passed.")

    if args.strict:
        failed_strict = [r for r in results if not r.passed]
        if failed_strict:
            return 1
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
