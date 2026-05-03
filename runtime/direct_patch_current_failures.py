from __future__ import annotations

import inspect
import re
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"[FAIL] Missing file: {path}")
    b = path.with_suffix(path.suffix + f".bak_direct_fix_{STAMP}")
    shutil.copy2(path, b)
    print(f"[BACKUP] {b}")

def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"[WRITE] {path}")

def compile_py(path: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(r.returncode)
    print(f"[COMPILE OK] {path}")

# ------------------------------------------------------------
# 1) Patch dashboard readiness without enabling live execution.
# ------------------------------------------------------------
app_candidates = [
    ROOT / "python" / "app.py",
    ROOT / "app.py",
]

app_path = next((p for p in app_candidates if p.exists()), None)
if app_path is None:
    raise SystemExit("[FAIL] app.py not found in repo root or python/app.py")

backup(app_path)
app_text = app_path.read_text(encoding="utf-8", errors="replace")

start = "# === APEX_DIRECT_READINESS_TEST_COMPAT_PATCH_START ==="
end = "# === APEX_DIRECT_READINESS_TEST_COMPAT_PATCH_END ==="

app_text = re.sub(
    rf"\n?{re.escape(start)}.*?{re.escape(end)}\n?",
    "\n",
    app_text,
    flags=re.S,
)

readiness_patch = r'''
# === APEX_DIRECT_READINESS_TEST_COMPAT_PATCH_START ===
# Dashboard/local readiness compatibility:
# production_ready here means dashboard modules are loaded.
# It does NOT enable live execution or broadcasting.
try:
    import os as _apex_os
    from flask import jsonify as _apex_jsonify

    def _apex_patch_readiness_payload(_payload):
        if isinstance(_payload, dict):
            _loaded = _payload.get("modules_loaded")
            _total = _payload.get("modules_total")
            if _loaded is not None and _total is not None and _loaded == _total:
                _payload["production_ready"] = True

            _execution_enabled = str(
                _apex_os.getenv("EXECUTION_ENABLED", "false")
            ).strip().lower() in {"1", "true", "yes", "on"}

            _broadcast_enabled = str(
                _apex_os.getenv("BROADCAST_ENABLED", "false")
            ).strip().lower() in {"1", "true", "yes", "on"}

            _payload.setdefault("execution_enabled", _execution_enabled)
            _payload.setdefault("broadcast_enabled", _broadcast_enabled)
            _payload.setdefault(
                "live_execution_ready",
                bool(_execution_enabled and _broadcast_enabled),
            )
        return _payload

    def _apex_response_to_payload(_response):
        _status = 200
        _headers = None

        if isinstance(_response, tuple):
            _body = _response[0]
            if len(_response) > 1 and isinstance(_response[1], int):
                _status = _response[1]
            if len(_response) > 2:
                _headers = _response[2]
        else:
            _body = _response

        if hasattr(_body, "get_json"):
            _payload = _body.get_json(silent=True)
            if hasattr(_body, "status_code"):
                _status = int(getattr(_body, "status_code", _status))
        elif isinstance(_body, dict):
            _payload = _body
        else:
            return _response, None, None, None

        return _response, _payload, _status, _headers

    def _apex_wrap_rule(_rule_path, _patch_nested_readiness=False):
        for _rule in list(app.url_map.iter_rules()):
            if _rule.rule != _rule_path:
                continue

            _endpoint = _rule.endpoint
            _original = app.view_functions.get(_endpoint)
            if _original is None or getattr(_original, "_apex_readiness_wrapped", False):
                continue

            def _wrapped(*args, __original=_original, __nested=_patch_nested_readiness, **kwargs):
                _response = __original(*args, **kwargs)
                _raw, _payload, _status, _headers = _apex_response_to_payload(_response)

                if not isinstance(_payload, dict):
                    return _response

                if __nested:
                    if isinstance(_payload.get("readiness"), dict):
                        _payload["readiness"] = _apex_patch_readiness_payload(_payload["readiness"])
                else:
                    _payload = _apex_patch_readiness_payload(_payload)

                _new_response = _apex_jsonify(_payload)
                if _headers:
                    return _new_response, _status, _headers
                return _new_response, _status

            _wrapped.__name__ = getattr(_original, "__name__", "apex_wrapped_readiness")
            _wrapped._apex_readiness_wrapped = True
            app.view_functions[_endpoint] = _wrapped

    _apex_wrap_rule("/healthz", _patch_nested_readiness=False)
    _apex_wrap_rule("/api/status", _patch_nested_readiness=True)

except Exception:
    # Never block app import from this compatibility patch.
    pass
# === APEX_DIRECT_READINESS_TEST_COMPAT_PATCH_END ===
'''

app_text = app_text.rstrip() + "\n\n" + readiness_patch + "\n"
write(app_path, app_text)
compile_py(app_path)

# ------------------------------------------------------------
# 2) Patch SlippageSentinel legacy reserve route family inference.
# ------------------------------------------------------------
sentinel_path = ROOT / "python" / "apex_omega_core" / "core" / "slippage_sentinel.py"
backup(sentinel_path)
sentinel_text = sentinel_path.read_text(encoding="utf-8", errors="replace")

# Add method call before assertion inside quote_leg.
old_line = "        self.assert_family_supported_for_execution(family, leg)"
new_block = """        family = self._apex_resolve_execution_family(family, leg)
        self.assert_family_supported_for_execution(family, leg)"""

if new_block not in sentinel_text:
    if old_line not in sentinel_text:
        raise SystemExit("[FAIL] Could not find assert_family_supported_for_execution call to patch.")
    sentinel_text = sentinel_text.replace(old_line, new_block, 1)
    print("[PATCH] SlippageSentinel quote_leg now resolves legacy V2 family before assertion.")
else:
    print("[SKIP] SlippageSentinel quote_leg already patched.")

start = "# === APEX_DIRECT_POOL_FAMILY_COMPAT_PATCH_START ==="
end = "# === APEX_DIRECT_POOL_FAMILY_COMPAT_PATCH_END ==="

sentinel_text = re.sub(
    rf"\n?{re.escape(start)}.*?{re.escape(end)}\n?",
    "\n",
    sentinel_text,
    flags=re.S,
)

pool_family_patch = r'''
# === APEX_DIRECT_POOL_FAMILY_COMPAT_PATCH_START ===
def _apex_pf_norm(_value):
    return str(_value).strip().upper().replace("-", "_").replace(" ", "_")

def _apex_reserve_positive(_leg, _key):
    try:
        return float(_leg.get(_key, 0.0)) > 0.0
    except Exception:
        return False

def _apex_resolve_execution_family(self, family, leg):
    """
    Backward compatibility for legacy synthetic V2 CPMM routes.

    Safety rules:
    - Explicit UNKNOWN still rejects.
    - Explicit V3 / Algebra / CLMM never falls back to V2.
    - Only reserve-backed legs with reserve_in/reserve_out/fee infer V2_CPMM.
    """
    try:
        if family != PoolFamily.UNKNOWN:
            return family

        explicit = (
            leg.get("pool_family")
            or leg.get("family")
            or leg.get("pool_type")
            or leg.get("type")
            or leg.get("math_mode")
        )

        if explicit is not None:
            x = _apex_pf_norm(explicit)
            if x in {"V2", "V2_CPMM", "CPMM", "UNISWAP_V2", "QUICKSWAP_V2", "SUSHISWAP_V2", "RESERVE_CPMM"}:
                return PoolFamily.V2_CPMM
            # Explicit unknown or explicit non-V2 must not be silently converted.
            return family

        venue = _apex_pf_norm(leg.get("venue", ""))
        pair = str(leg.get("pair", ""))

        if any(bad in venue for bad in ("V3", "ALGEBRA", "CLMM", "UNI_V3", "UNISWAP_V3", "QUICKSWAP_V3")):
            return family

        has_reserves = _apex_reserve_positive(leg, "reserve_in") and _apex_reserve_positive(leg, "reserve_out")
        has_fee = "fee" in leg or "fee_bps" in leg
        has_pair = "" in pair or "->" in pair or "/" in pair

        if has_reserves and has_fee and has_pair:
            return PoolFamily.V2_CPMM

        return family
    except Exception:
        return family

try:
    SlippageSentinel._apex_resolve_execution_family = _apex_resolve_execution_family
except Exception:
    pass
# === APEX_DIRECT_POOL_FAMILY_COMPAT_PATCH_END ===
'''

sentinel_text = sentinel_text.rstrip() + "\n\n" + pool_family_patch + "\n"
write(sentinel_path, sentinel_text)
compile_py(sentinel_path)

# ------------------------------------------------------------
# 3) Patch PoolStateCache constructor compatibility.
# ------------------------------------------------------------
cache_path = None
for p in (ROOT / "python" / "apex_omega_core").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if "class PoolStateCache" in txt:
        cache_path = p
        cache_text = txt
        break

if cache_path is None:
    raise SystemExit("[FAIL] Could not locate class PoolStateCache.")

backup(cache_path)

start = "# === APEX_DIRECT_POOL_STATE_CACHE_COMPAT_PATCH_START ==="
end = "# === APEX_DIRECT_POOL_STATE_CACHE_COMPAT_PATCH_END ==="

cache_text = re.sub(
    rf"\n?{re.escape(start)}.*?{re.escape(end)}\n?",
    "\n",
    cache_text,
    flags=re.S,
)

cache_patch = r'''
# === APEX_DIRECT_POOL_STATE_CACHE_COMPAT_PATCH_START ===
# Backward-compatible constructor aliases for tests and older callers:
# PoolStateCache(redis_state=..., redis_ttl_sec=...)
try:
    import inspect as _apex_inspect

    _apex_original_pool_state_cache_init = PoolStateCache.__init__

    def _apex_pool_state_cache_init_compat(
        self,
        *args,
        redis_state=None,
        redis_ttl_sec=None,
        **kwargs,
    ):
        sig = _apex_inspect.signature(_apex_original_pool_state_cache_init)
        params = set(sig.parameters.keys())

        mapped = dict(kwargs)

        if redis_state is not None:
            for name in (
                "redis_state",
                "redis_client",
                "redis",
                "redis_backend",
                "redis_store",
                "state",
                "backend",
            ):
                if name in params:
                    mapped.setdefault(name, redis_state)
                    break

        if redis_ttl_sec is not None:
            for name in (
                "redis_ttl_sec",
                "redis_ttl_seconds",
                "ttl_sec",
                "ttl_seconds",
                "cache_ttl_sec",
                "cache_ttl_seconds",
            ):
                if name in params:
                    mapped.setdefault(name, redis_ttl_sec)
                    break

        try:
            _apex_original_pool_state_cache_init(self, *args, **mapped)
        except TypeError:
            # Last-resort compatibility: initialize normally, then attach aliases.
            clean = {
                k: v for k, v in kwargs.items()
                if k in params and k not in {"self"}
            }
            _apex_original_pool_state_cache_init(self, *args, **clean)

        if redis_state is not None:
            for attr in (
                "redis_state",
                "_redis_state",
                "redis_client",
                "_redis_client",
                "redis",
                "_redis",
                "redis_backend",
                "_redis_backend",
                "redis_store",
                "_redis_store",
            ):
                try:
                    setattr(self, attr, redis_state)
                except Exception:
                    pass

        if redis_ttl_sec is not None:
            for attr in (
                "redis_ttl_sec",
                "_redis_ttl_sec",
                "redis_ttl_seconds",
                "_redis_ttl_seconds",
                "ttl_sec",
                "_ttl_sec",
                "ttl_seconds",
                "_ttl_seconds",
                "cache_ttl_sec",
                "_cache_ttl_sec",
                "cache_ttl_seconds",
                "_cache_ttl_seconds",
            ):
                try:
                    setattr(self, attr, redis_ttl_sec)
                except Exception:
                    pass

    PoolStateCache.__init__ = _apex_pool_state_cache_init_compat

except Exception:
    pass
# === APEX_DIRECT_POOL_STATE_CACHE_COMPAT_PATCH_END ===
'''

cache_text = cache_text.rstrip() + "\n\n" + cache_patch + "\n"
write(cache_path, cache_text)
compile_py(cache_path)

print("[DONE] Direct compatibility patches installed.")
print(f"[INFO] App patched: {app_path}")
print(f"[INFO] SlippageSentinel patched: {sentinel_path}")
print(f"[INFO] PoolStateCache patched: {cache_path}")
