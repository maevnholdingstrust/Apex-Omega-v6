"""Optional public RPC discovery client for read-side fallback infrastructure."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

_CACHE: dict[int, tuple[float, list[str]]] = {}


def _enabled() -> bool:
    return os.getenv("DODO_RPC_DISCOVERY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _as_positive_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _as_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _valid_public_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or "${" in value:
        return None
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def discover_public_rpc_urls(chain_id: int) -> list[str]:
    """Return cached DODO-discovered public RPC URLs for read-side fallback use."""
    if not _enabled():
        return []

    now = time.monotonic()
    ttl_s = _as_positive_float("DODO_RPC_DISCOVERY_CACHE_TTL_SEC", 300.0)
    cached = _CACHE.get(chain_id)
    if cached and now - cached[0] < ttl_s:
        return list(cached[1])

    base_url = os.getenv("DODO_RPC_PROVIDER_URL", "http://127.0.0.1:3000").rstrip("/")
    sources = [
        source.strip()
        for source in os.getenv("DODO_RPC_DISCOVERY_SOURCES", "ChainList").split(",")
        if source.strip()
    ]
    query = urllib.parse.urlencode([("sources[]", source) for source in sources])
    request_url = f"{base_url}/{chain_id}/endpoints?{query}"
    timeout_s = _as_positive_float("DODO_RPC_DISCOVERY_TIMEOUT_SEC", 2.0)
    max_endpoints = _as_positive_int("DODO_RPC_DISCOVERY_MAX_ENDPOINTS", 12)

    try:
        with urllib.request.urlopen(request_url, timeout=timeout_s) as response:
            payload = json.load(response)
    except Exception:
        return list(cached[1]) if cached else []

    urls: list[str] = []
    seen: set[str] = set()
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or int(item.get("chainId", 0)) != chain_id:
            continue
        url = _valid_public_http_url(item.get("url"))
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
        if len(urls) >= max_endpoints:
            break

    _CACHE[chain_id] = (now, urls)
    return list(urls)
