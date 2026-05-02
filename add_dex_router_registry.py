from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys

ROOT = Path.cwd()
CORE = ROOT / "python" / "apex_omega_core" / "core"
path = CORE / "dex_router_registry.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

if path.exists():
    backup = path.with_suffix(path.suffix + f".bak_{STAMP}")
    shutil.copy2(path, backup)
    print(f"[BACKUP] {backup}")

content = r'''
from __future__ import annotations

import os
from typing import Optional


DEFAULT_DEX_ROUTERS = {
    "quickswap-v2": "0xa5E0829CaCED8fFDD4De3c43696c57F7D7A678ff",
    "sushiswap": "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506",
    "uniswap-v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "quickswap-v3": "0xf5b509bB0909a69B1c207E495f687a596C168E12",
    "apeswap": "0xC0788A3aD43d79aa53B09c2EaCc313A787d1d607",
    "dfyn": "0xA102072A4C07F06EC3B4900FDC4C7B80FbbdC5C7",
    "elk-finance": "0x9E2A10A9b83Df57bA067C2D3e7d79bdb3B516C5B",
    "dodo": "0xa356867fDCEa8e71AEaF87805808803806231FdC",
    "kyberswap-classic": "0x546C79662E028B661dFB4767664d0273184E4Dd1",
    "curve": "0x0dcded3545d565ba3b19e683431381007245d983",
    "paraswap": "0xDEF171Fe48CF0148B1a80588e89784919975a401",
    "woofi": "0x56d8aB6E4C708B40828b9DaaabF62c22bC4e46F5",
}

ROUTER_ENV_ALIASES = {
    "quickswap-v2": ["QUICKSWAP_ROUTER"],
    "quickswap-v3": ["QUICKSWAP_V3_ROUTER", "QUICKSWAP_ROUTER_V3"],
    "sushiswap": ["SUSHISWAP_ROUTER"],
    "uniswap-v3": ["UNISWAP_V3_ROUTER", "UNISWAPV3_ROUTER", "SWAP_ROUTER_02"],
    "apeswap": ["APE_SWAP_ROUTER", "APESWAP_ROUTER"],
    "dfyn": ["DFYN_ROUTER"],
    "elk-finance": ["ELK_ROUTER", "ELK_FINANCE_ROUTER"],
    "dodo": ["DODO_ROUTER"],
    "kyberswap-classic": ["KYBERDMM_ROUTER", "KYBERSWAP_CLASSIC_ROUTER"],
    "curve": ["CURVE_ROUTER"],
    "balancer-v2": ["BALANCER_VAULT", "BALANCER_VAULT_V2"],
    "balancer-v3": ["BALANCER_VAULT_V3"],
    "paraswap": ["PARASWAP_ROUTER", "PARASWAP_PROXY"],
    "woofi": ["WOOFI_ROUTER"],
    "meshswap": ["MESHSWAP_ROUTER"],
    "retro": ["RETRO_ROUTER"],
}


def normalize_dex_name(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def get_router_address(dex_name: str) -> Optional[str]:
    key = normalize_dex_name(dex_name)

    for env_key in ROUTER_ENV_ALIASES.get(key, []):
        value = os.getenv(env_key)
        if value:
            return value

    return DEFAULT_DEX_ROUTERS.get(key)


def require_router_address(dex_name: str) -> str:
    router = get_router_address(dex_name)
    if not router:
        raise ValueError(f"Missing router address for dex={dex_name}")
    return router


def known_router_addresses() -> set[str]:
    addresses = set(DEFAULT_DEX_ROUTERS.values())

    for aliases in ROUTER_ENV_ALIASES.values():
        for env_key in aliases:
            value = os.getenv(env_key)
            if value:
                addresses.add(value)

    return {a.lower() for a in addresses if isinstance(a, str) and a.startswith("0x")}


def is_known_router(address: str) -> bool:
    return str(address or "").lower() in known_router_addresses()
'''

path.write_text(content, encoding="utf-8", newline="\n")

result = subprocess.run(
    [sys.executable, "-m", "py_compile", str(path)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print(result.stderr)
    raise SystemExit(result.returncode)

print("[OK] dex_router_registry.py installed")
print("[DONE] Router defaults + env alias resolver ready")
