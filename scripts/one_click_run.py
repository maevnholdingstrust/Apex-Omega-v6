#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, env=env)


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

    env = os.environ.copy()
    env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] = "1"

    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], cwd=root, env=env)
    editable = run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=root, check=False, env=env)
    if editable.returncode != 0:
        print("\nWARNING: editable Rust/PyO3 build failed. Continuing with Python-only path.")
        print("Recommended: install Python 3.12 for full Rust extension support.")
        src_path = str(py_dir)
        os.environ["PYTHONPATH"] = src_path + os.pathsep + os.environ.get("PYTHONPATH", "")
    run([sys.executable, "-m", "pip", "install", "web3", "python-dotenv", "eth-abi", "pytest", "numpy", "pandas", "requests"], cwd=root, env=env)


def write_dry_run_script(root: Path) -> Path:
    script = root / "scripts" / "_generated_live_market_dry_run.py"
    script.write_text(
        r'''
from apex_omega_core.core.runtime_config import load_runtime_config
from apex_omega_core.core.rpc_tester import get_canonical_two_leg_state
from apex_omega_core.core.slippage_sentinel import SlippageSentinel
from apex_omega_core.core.live_strategy_steps import build_live_strategy_output_from_state

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
    c_gas=0.0,
    c_loan=flash_fee,
    c_other=config.risk_buffer_usd,
)
owner_submission_edge = result["p_net"] - config.c2_gas_usd

print("\n=== APEX-OMEGA LIVE MARKET DRY RUN ===")
print(f"chain_id: {config.chain_id}")
print(f"live_trading_enabled: {config.live_trading_enabled}")
print(f"dry_run: {config.dry_run}")
print("\n--- Pool State ---")
print(f"rpc_url: {state.get('rpc_url', '')}")
print(f"fee1: {fee1}")
print(f"r1_in: {r1_in}")
print(f"r1_out: {r1_out}")
print(f"fee2: {fee2}")
print(f"r2_in: {r2_in}")
print(f"r2_out: {r2_out}")
for key in ["qsv2_usdc_per_wmatic", "uv3_usdc_per_wmatic", "raw_spread_usdc_per_wmatic", "raw_spread_bps"]:
    if key in state:
        print(f"{key}: {state[key]}")
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
print(f"owner_submission_edge: {owner_submission_edge}")
print("\n--- Decision ---")
print("DECISION: STRIKE" if result["p_net"] > config.min_net_profit_usd else "DECISION: IDLE")

strategy_build = build_live_strategy_output_from_state(
    state,
    executor_address=config.c1_executor_address or "0x0000000000000000000000000000000000000000",
    min_net_profit_usd=config.min_net_profit_usd,
    gas_cost_usd=config.c1_gas_usd,
    flash_fee_bps=config.flash_loan_fee_bps,
    risk_buffer_usd=config.risk_buffer_usd,
)
print("\n=== STRATEGY BUILD ===")
print("strikeable:", strategy_build.strikeable)
print("reason:", strategy_build.reason)
if strategy_build.diagnostics:
    for key, value in strategy_build.diagnostics.items():
        print(f"{key}: {value}")
if strategy_build.strategy_output:
    print("steps:", len(strategy_build.strategy_output["steps"]))
    print("payload_len:", strategy_build.compiled_payload_len)
    print("min_profit:", strategy_build.min_profit)
''',
        encoding="utf-8",
    )
    return script


def write_discovery_script(root: Path) -> Path:
    script = root / "scripts" / "_generated_discovery_scan.py"
    script.write_text(
        r'''"""Expanded multi-hop discovery scan — run via one_click_run.py."""
import asyncio
import sys
from pathlib import Path

# Ensure python/ is importable even when run directly.
_python_dir = str(Path(__file__).resolve().parents[1] / "python")
if _python_dir not in sys.path:
    sys.path.insert(0, _python_dir)


async def _main() -> None:
    try:
        from dry_run import run_live_opportunity_scan
    except ImportError as exc:
        print(f"Cannot import dry_run: {exc}")
        print("Ensure PYTHONPATH includes the python/ directory.")
        return

    print("\n=== EXPANDED MULTI-HOP DISCOVERY SCAN ===")
    print("Searching 2–4 hop arbitrage cycles across live Polygon pools (1 pass).")
    try:
        records = await run_live_opportunity_scan(
            max_scans=1,
            target_count=10,
            enable_expanded_scan=True,
        )
        profitable = [r for r in records if r.profitable]
        print(
            f"\nDiscovery complete: {len(records)} routes sampled, "
            f"{len(profitable)} profitable (net>0)"
        )
        for r in sorted(profitable, key=lambda x: x.e_profit, reverse=True)[:5]:
            hops = getattr(r, "hop_count", "?")
            print(
                f"  {r.pair}  hops={hops}  "
                f"net=${r.expected_net_edge:+.4f}  E[profit]=${r.e_profit:.4f}"
            )
        if not profitable:
            print(
                "  No profitable multi-hop routes found this pass — "
                "markets are efficient or trade sizes need tuning."
            )
    except ConnectionError as exc:
        print(f"\nDiscovery scan skipped (no RPC): {exc}")
        print("Set POLYGON_RPC (or POLYGON_HTTP / ALCHEMY_HTTP_1) and retry.")
    except Exception as exc:
        print(f"\nDiscovery scan error: {exc}")


asyncio.run(_main())
''',
        encoding="utf-8",
    )
    return script


def main() -> int:
    root = repo_root()
    ensure_env(root)
    install_python_package(root)

    env = os.environ.copy()
    env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] = "1"
    env["PYTHONPATH"] = str(root / "python") + os.pathsep + env.get("PYTHONPATH", "")

    print("\n=== RPC HEALTH CHECK ===")
    run([sys.executable, "-m", "apex_omega_core.core.rpc_tester"], cwd=root, check=False, env=env)

    print("\n=== LIVE MARKET DRY RUN ===")
    dry_script = write_dry_run_script(root)
    result = run([sys.executable, str(dry_script)], cwd=root, check=False, env=env)

    print("\n=== EXPANDED MULTI-HOP DISCOVERY ===")
    disc_script = write_discovery_script(root)
    run([sys.executable, str(disc_script)], cwd=root, check=False, env=env)

    print("\n=== DONE ===")
    print("If dry-run failed, check .env POLYGON_RPC and dependencies.")
    print("Safe defaults keep LIVE_TRADING_ENABLED=false and DRY_RUN=true.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
