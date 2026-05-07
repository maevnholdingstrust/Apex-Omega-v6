"""Signal schema normalizer for Apex-Omega-v6.

Inbound signals from external sources (scanner outputs, relay feeds, off-chain
bots) may use a variety of field naming conventions.  This module provides a
single ``normalize_signal`` entry point that maps known legacy key variants to
the canonical schema before any downstream logic runs.

Canonical output schema
-----------------------
The returned dict always uses these top-level keys:

``chainId``
    EIP-155 chain ID as an integer (e.g. ``137`` for Polygon mainnet).
``token``
    Checksummed address of the flash-loan / input token.
``amount``
    Input amount in token-native units (human-readable float or integer).
``metrics``
    Sub-dict with profit and signal quality fields:

    ``profit_usd``
        Estimated or realized net profit in USD.

All other keys that already appear in the raw signal are passed through
unchanged so that callers receive a superset of the original data.

Normalization rules applied (in order)
---------------------------------------
1. ``chain_id``             → ``chainId``
2. ``execution.input_token`` (nested)  → ``token``  (top-level)
3. ``execution.input_amount`` (nested) → ``amount`` (top-level)
4. ``profit.net_usd``        (nested)  → ``metrics.profit_usd`` (nested)

Rules are applied non-destructively: the *source* key is removed from the
output only when the mapping is unambiguous.  If the canonical key is already
present the source key is still removed (no double-writes).
"""

from __future__ import annotations

from typing import Any, Dict

# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

POLYGON_CHAIN_ID: int = 137
"""EIP-155 chain ID for Polygon mainnet."""


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def normalize_signal(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *raw* with legacy field names replaced by canonical ones.

    Parameters
    ----------
    raw:
        Arbitrary signal dict from an external source.  The dict is never
        mutated in place; a shallow copy is taken first.

    Returns
    -------
    dict
        Normalized signal dict.  All keys not covered by a normalization rule
        are forwarded verbatim.

    Examples
    --------
    Legacy schema with nested execution / profit blocks::

        normalize_signal({
            "chain_id": 137,
            "execution": {"input_token": "0xabc", "input_amount": 1000},
            "profit": {"net_usd": 5.23},
        })
        # → {
        #     "chainId": 137,
        #     "token": "0xabc",
        #     "amount": 1000,
        #     "metrics": {"profit_usd": 5.23},
        # }

    Mixed / partially canonical schema::

        normalize_signal({
            "chainId": 137,
            "token": "0xabc",
            "amount": 1000,
            "profit": {"net_usd": 5.23},
            "extra_field": "kept",
        })
        # → {
        #     "chainId": 137,
        #     "token": "0xabc",
        #     "amount": 1000,
        #     "metrics": {"profit_usd": 5.23},
        #     "extra_field": "kept",
        # }
    """
    out: Dict[str, Any] = dict(raw)

    # Rule 1 — chain_id → chainId
    if "chain_id" in out and "chainId" not in out:
        out["chainId"] = out.pop("chain_id")
    elif "chain_id" in out:
        # Both present: canonical key wins; drop the legacy key.
        del out["chain_id"]

    # Rule 2 & 3 — execution.{input_token,input_amount} → token / amount
    if "execution" in out and isinstance(out["execution"], dict):
        exec_block: Dict[str, Any] = out.pop("execution")
        if "input_token" in exec_block and "token" not in out:
            out["token"] = exec_block.pop("input_token")
        elif "input_token" in exec_block:
            del exec_block["input_token"]

        if "input_amount" in exec_block and "amount" not in out:
            out["amount"] = exec_block.pop("input_amount")
        elif "input_amount" in exec_block:
            del exec_block["input_amount"]

        # Re-attach any remaining keys from the execution block.
        if exec_block:
            out["execution"] = exec_block

    # Rule 4 — profit.net_usd → metrics.profit_usd
    if "profit" in out and isinstance(out["profit"], dict):
        profit_block: Dict[str, Any] = out["profit"]
        if "net_usd" in profit_block:
            net_usd = profit_block.pop("net_usd")
            metrics: Dict[str, Any] = out.get("metrics") or {}
            if not isinstance(metrics, dict):
                metrics = {}
            if "profit_usd" not in metrics:
                metrics["profit_usd"] = net_usd
            out["metrics"] = metrics
        # Drop empty profit block; keep it if other sub-keys remain.
        if not profit_block:
            del out["profit"]

    return out
