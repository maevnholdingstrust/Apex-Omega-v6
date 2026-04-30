#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_env(root: Path) -> None:
    env_path = root / ".env"
    example_path = root / ".env.example"
    if not env_path.exists() and example_path.exists():
        env_path.write_text(example_path.read_text(), encoding="utf-8")
        print("Created .env from .env.example. Fill POLYGON_RPC before live dry-run.")


def install_python_package(root: Path) -> None:
    py_dir = root / "python"
    if not (root / "pyproject.toml").exists():
        raise SystemExit("Repository pyproject.toml not found")
    if not py_dir.exists():
        raise SystemExit("python/ directory not found")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
    run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=root)
    run([sys.executable, "-m", "pip", "install", "web3", "python-dotenv", "eth-abi", "pytest"], cwd=root)


def write_dry_run_script(root: Path) -> Path:
    script = root / "scripts" / "_generated_live_market_dry_run.py"
    script.write_text(
        r'''
from apex_omega_core.core.runtime_config import load_runtime_config
from apex_omega_core.core.rpc_tester import get_canonical_two_leg_state
from apex_omega_core.core.slippage_sentinel import SlippageSentinel

config = load_runtime_config()
sentinel = SlippageSentinel()

state = get_canonical_two_leg_state()
fee1 = float(state["fee1"])
r1_in = float(state["r1_in"])
r1_out = float(state["r1_out"])
fee2 = float(state["fee2"])
r2_in = float(state["r2_in"])
r2_out = float(state["r2_out"])

amount_in = sentinel.optimal_two_leg_input(r1_in, r1_out, fee1, r2_in, r2_out, fee2)
flash_fee = amount_in * (config.flash_loan_fee_bps / 10_000.0)
result = sentinel.two_leg_arb_profit(
    amount_in,
    fee1, r1_in, r1_out,
    fee2, r2_in, r2_out,
    c_gas=config.c2_gas_usd,
    c_loan=flash_fee,
    c_other=config.risk_buffer_usd,
)

print("\n=== APEX-OMEGA LIVE MARKET DRY RUN ===")
print(f"chain_id: {config.chain_id}")
print(f"live_trading_enabled: {config.live_trading_enabled}")
print(f"dry_run: {config.dry_run}")
print("\n--- Pool State ---")
print(f"fee1: {fee1}")
print(f"r1_in: {r1_in}")
print(f"r1_out: {r1_out}")
print(f"fee2: {fee2}")
print(f"r2_in: {r2_in}")
print(f"r2_out: {r2_out}")
print("\n--- Leg Math ---")
print(f"optimal_amount_in: {amount_in}")
print(f"leg1_b_out: {result['b_out_1']}")
print(f"leg2_a_out: {result['a_out_2']}")
print("\n--- Costs / PnL ---")
print(f"gross_profit: {result['p_gross']}")
print(f"flash_fee: {flash_fee}")
print(f"gas_cost: {config.c2_gas_usd}")
print(f"risk_buffer: {config.risk_buffer_usd}")
print(f"net_profit: {result['p_net']}")
print("\n--- Decision ---")
print("DECISION: STRIKE" if result["p_net"] > config.min_net_profit_usd else "DECISION: IDLE")
''',
        encoding="utf-8",
    )
    return script


def main() -> int:
    root = repo_root()
    ensure_env(root)
    install_python_package(root)

    print("\n=== RPC HEALTH CHECK ===")
    run([sys.executable, "-m", "apex_omega_core.core.rpc_tester"], cwd=root, check=False)

    print("\n=== LIVE MARKET DRY RUN ===")
    dry_script = write_dry_run_script(root)
    result = run([sys.executable, str(dry_script)], cwd=root, check=False)

    print("\n=== DONE ===")
    print("If dry-run failed, check .env POLYGON_RPC and dependencies.")
    print("Safe defaults keep LIVE_TRADING_ENABLED=false and DRY_RUN=true.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
