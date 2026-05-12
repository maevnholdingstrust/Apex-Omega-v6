"""Lightweight Flask API server for the Apex-Omega v6 backend.

Exposes the executor registry and startup validation results as JSON
endpoints.  All contract metadata is sourced from
:mod:`backend.executor_registry`; no addresses or ABIs are hard-coded in
this module.

Endpoints
---------
``GET  /api/registry``
    List all executor registry entries.
``GET  /api/registry/<chain_id>/<strategy>``
    Get a single registry entry.
``POST /api/validate``
    Run startup validation for all configured entries and return results.
``POST /api/validate/<chain_id>/<strategy>``
    Validate a single entry.
``GET  /api/chains``
    List supported chains.
``GET  /api/health``
    Basic health check (always 200 when the server is up).

Running
-------
::

    python -m backend.server            # development
    gunicorn backend.server:app         # production
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from flask import Flask, Response, jsonify, request

from backend.executor_registry import (
    EXECUTOR_REGISTRY,
    SUPPORTED_CHAINS,
    ValidationResult,
    get_entry,
    get_rpc_url,
    list_entries,
    validate_all,
    validate_registry_entry,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json(payload: Any, status: int = 200) -> Tuple[Response, int]:
    return jsonify(payload), status


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> Tuple[Response, int]:
    """Return a 200 health check."""
    return _json({"status": "ok"})


@app.get("/api/chains")
def chains() -> Tuple[Response, int]:
    """List supported chains."""
    return _json(
        [
            {
                "chain_id": c.chain_id,
                "name": c.name,
                "native_symbol": c.native_symbol,
                "rpc_env_var": c.rpc_env_var,
                "block_time_s": c.block_time_s,
            }
            for c in SUPPORTED_CHAINS.values()
        ]
    )


@app.get("/api/registry")
def registry_list() -> Tuple[Response, int]:
    """List all executor registry entries."""
    return _json([entry.as_dict() for entry in list_entries()])


@app.get("/api/registry/<int:chain_id>/<string:strategy>")
def registry_entry(chain_id: int, strategy: str) -> Tuple[Response, int]:
    """Get a single registry entry by chain ID and strategy name."""
    try:
        entry = get_entry(chain_id, strategy)
    except KeyError as exc:
        return _json({"error": str(exc)}, 404)
    return _json(entry.as_dict())


@app.post("/api/validate")
def validate_all_entries() -> Tuple[Response, int]:
    """Run startup validation for every configured registry entry.

    Optional JSON body
    ------------------
    ``{"chain_id": 137}`` — restrict validation to a single chain.
    ``{"rpc_url": "https://..."}`` — override RPC URL.
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    chain_id: int | None = body.get("chain_id")
    rpc_url: str | None = body.get("rpc_url")

    results = validate_all(chain_id=chain_id, rpc_url=rpc_url)
    status = 200 if all(r.passed for r in results) else 207
    return _json([r.as_dict() for r in results], status)


@app.post("/api/validate/<int:chain_id>/<string:strategy>")
def validate_entry(chain_id: int, strategy: str) -> Tuple[Response, int]:
    """Validate a single registry entry.

    Optional JSON body
    ------------------
    ``{"rpc_url": "https://..."}`` — override RPC URL.
    """
    try:
        entry = get_entry(chain_id, strategy)
    except KeyError as exc:
        return _json({"error": str(exc)}, 404)

    body: Dict[str, Any] = request.get_json(silent=True) or {}
    rpc_url: str | None = body.get("rpc_url")

    result: ValidationResult = validate_registry_entry(entry, rpc_url=rpc_url)
    status = 200 if result.passed else 424
    return _json(result.as_dict(), status)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    logger.info("Starting Apex-Omega backend server on %s:%d", host, port)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
