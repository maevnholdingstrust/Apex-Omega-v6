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

import argparse
import json
import logging
import os
import sys
from pathlib import Path
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


def _load_dotenv_if_available() -> List[str]:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    cwd = Path.cwd()
    candidates = [
        cwd / ".env",
        cwd / "python" / "apex_omega_core" / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    loaded: List[str] = []
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            loaded.append(str(path))
            break
    if loaded:
        logger.info("Loaded startup environment from %s", loaded[0])
    else:
        logger.info("No dotenv file detected; using process environment only.")
    return loaded


def _first_non_empty_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "")
        if value:
            return value
    return ""


def _set_if_missing(target: str, value: str) -> bool:
    if os.getenv(target) or not value:
        return False
    os.environ[target] = value
    return True


def _configure_startup_env() -> Dict[str, Any]:
    """Normalize startup env aliases to canonical backend keys."""
    loaded_dotenv = _load_dotenv_if_available()
    aliases_applied: Dict[str, str] = {}

    alias_values = {
        "POLYGON_RPC": _first_non_empty_env(
            "POLYGON_RPC",
            "POLYGON_RPC_URL",
            "POLYGON_HTTP",
            "APEX_RPC_URL",
        ),
        "ETH_RPC": _first_non_empty_env("ETH_RPC", "ETH_RPC_URL", "ETHEREUM_RPC"),
        "C1_INSTITUTIONAL_EXECUTOR_ADDRESS": _first_non_empty_env(
            "C1_INSTITUTIONAL_EXECUTOR_ADDRESS",
            "EXECUTOR_C1_ADDRESS",
            "C1_TARGET",
        ),
        "C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS": _first_non_empty_env(
            "C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS",
            "EXECUTOR_C2_ADDRESS",
            "C2_TARGET",
        ),
        "EXECUTOR_PRIVATE_KEY": _first_non_empty_env(
            "EXECUTOR_PRIVATE_KEY",
            "PRIVATE_KEY",
            "APEX_PRIVATE_KEY",
        ),
    }

    for key, value in alias_values.items():
        if _set_if_missing(key, value):
            aliases_applied[key] = "set"

    return {
        "aliases_applied": aliases_applied,
        "dotenv_loaded_from": loaded_dotenv,
    }


def _sanitize_startup_error(exc: Exception) -> str:
    """Return a non-sensitive startup error summary."""
    return (
        f"{exc.__class__.__name__} during live executor startup. "
        "Check dependency installation and environment configuration."
    )


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


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apex-Omega live executor startup validator and runtime bootstrap.",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.getenv("CHAIN_ID", "137")),
        help="EVM chain ID (default: CHAIN_ID env or 137).",
    )
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="Optional RPC URL override.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any startup validation check fails.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable logs.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    startup_config = _configure_startup_env()

    try:
        executor = LiveExecutor(chain_id=args.chain_id, rpc_url=args.rpc_url)
        results = executor.startup_validate()
    except Exception as exc:
        logger.debug("Live executor startup exception details", exc_info=True)
        message = _sanitize_startup_error(exc)
        if args.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            logger.error("%s", message)
        return 2

    failed = [r for r in results if not r.passed]
    payload = {
        "ok": len(failed) == 0,
        "chain_id": executor.chain_id,
        "is_live": executor.is_live,
        "dry_run": executor._dry_run,
        "live_trading_enabled": executor._live_trading_enabled,
        "rpc_configured": bool(executor._rpc_url),
        "aliases_applied": startup_config["aliases_applied"],
        "dotenv_loaded_from": startup_config["dotenv_loaded_from"],
        "validation_total": len(results),
        "validation_failed": len(failed),
        "results": [r.as_dict() for r in results],
    }

    if args.json:
        print(json.dumps(payload))
    else:
        logger.info(
            "LiveExecutor startup: chain_id=%d is_live=%s dry_run=%s checks=%d failed=%d",
            executor.chain_id,
            executor.is_live,
            executor._dry_run,
            len(results),
            len(failed),
        )

    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        stream=sys.stdout,
    )
    raise SystemExit(main())
