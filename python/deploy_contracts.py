#!/usr/bin/env python3
"""deploy_contracts.py — Compile and deploy Apex-Omega executor contracts to Polygon.

Usage
-----
    # Dry-run (compile only, no deployment):
    python deploy_contracts.py --contract institutional --dry-run

    # Deploy InstitutionalExecutor to Polygon mainnet:
    python deploy_contracts.py --contract institutional \\
        --rpc-url $POLYGON_RPC_URL \\
        --private-key $PRIVATE_KEY

    # Deploy UltimateArbitrageExecutor:
    python deploy_contracts.py --contract ultimate \\
        --rpc-url $POLYGON_RPC_URL \\
        --private-key $PRIVATE_KEY

    # Verify on Polygonscan after deployment:
    python deploy_contracts.py --contract institutional \\
        --rpc-url $POLYGON_RPC_URL \\
        --private-key $PRIVATE_KEY \\
        --verify --polygonscan-key $POLYGONSCAN_API_KEY

Environment variables (all overridable via CLI flags)
------------------------------------------------------
POLYGON_RPC_URL      — HTTP RPC endpoint
PRIVATE_KEY          — Deployer EOA private key (0x-prefixed)
POLYGONSCAN_API_KEY  — For contract verification (optional)

Dependencies
------------
    pip install web3>=6.0.0 py-solc-x requests
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("deploy")

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"

CONTRACT_FILES: Dict[str, str] = {
    "institutional": "InstitutionalExecutor.sol",
    "ultimate":      "UltimateArbitrageExecutor.sol",
}

# Polygon mainnet chain ID
POLYGON_CHAIN_ID = 137

# ---------------------------------------------------------------------------
# Solidity compiler bootstrap
# ---------------------------------------------------------------------------

def _ensure_solc(version: str = "0.8.24") -> None:
    """Install solc via py-solc-x if not already present."""
    try:
        import solcx
        installed = solcx.get_installed_solc_versions()
        from packaging.version import Version
        target = Version(version)
        if not any(Version(str(v)) == target for v in installed):
            logger.info("Installing solc %s …", version)
            solcx.install_solc(version)
        solcx.set_solc_version(version)
        logger.info("solc %s ready.", version)
    except ImportError:
        logger.error(
            "py-solc-x is not installed. Run: pip install py-solc-x packaging"
        )
        sys.exit(1)


def _compile_contract(sol_path: Path) -> Tuple[str, str]:
    """Compile *sol_path* and return (abi_json_str, bytecode_hex).

    The contract source uses OpenZeppelin HTTP imports.  py-solc-x resolves
    these when ``allow_paths`` is set to the project root, but GitHub-hosted
    imports require network access at compile time.

    Returns (abi, bytecode) as strings.
    """
    try:
        import solcx
    except ImportError:
        logger.error("py-solc-x not installed. Run: pip install py-solc-x")
        sys.exit(1)

    logger.info("Compiling %s …", sol_path.name)
    source = sol_path.read_text()

    result = solcx.compile_source(
        source,
        output_values=["abi", "bin"],
        solc_version="0.8.24",
        optimize=True,
        optimize_runs=200,
    )

    # Pick the main contract entry (the one matching the filename stem).
    stem = sol_path.stem
    key = next(
        (k for k in result if k.split(":")[-1] == stem),
        next(iter(result)),
    )
    contract_data = result[key]
    abi = json.dumps(contract_data["abi"])
    bytecode = contract_data["bin"]
    logger.info("Compiled: %s (bytecode %d bytes)", key, len(bytecode) // 2)
    return abi, bytecode


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

def _deploy(
    abi: str,
    bytecode: str,
    rpc_url: str,
    private_key: str,
    gas_limit: int = 4_000_000,
) -> Dict[str, Any]:
    """Deploy a compiled contract and return the deployment result dict."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        logger.error("Cannot connect to RPC at %s", rpc_url)
        sys.exit(1)

    chain_id = w3.eth.chain_id
    logger.info("Connected to chain %d via %s", chain_id, rpc_url)

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    account = w3.eth.account.from_key(private_key)
    logger.info("Deployer: %s", account.address)

    balance_wei = w3.eth.get_balance(account.address)
    balance_pol = balance_wei / 1e18
    logger.info("Deployer balance: %.6f POL", balance_pol)
    if balance_pol < 0.01:
        logger.error(
            "Deployer balance %.6f POL is too low to cover gas. Top up first.",
            balance_pol,
        )
        sys.exit(1)

    # EIP-1559 gas params.
    fee_history = w3.eth.fee_history(5, "latest", [50])
    base_fee = fee_history.baseFeePerGas[-1]
    tip = 30 * 10 ** 9  # 30 Gwei priority fee — conservative for Polygon
    max_fee = base_fee * 2 + tip
    logger.info(
        "Gas params: base_fee=%.2f Gwei  tip=%.2f Gwei  max_fee=%.2f Gwei",
        base_fee / 1e9, tip / 1e9, max_fee / 1e9,
    )

    contract = w3.eth.contract(abi=json.loads(abi), bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)

    deploy_tx = contract.constructor().build_transaction({
        "chainId": chain_id,
        "from": account.address,
        "nonce": nonce,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": tip,
    })

    signed = w3.eth.account.sign_transaction(deploy_tx, private_key=private_key)
    logger.info("Broadcasting deployment transaction …")
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    tx_hash_hex = Web3.to_hex(tx_hash)
    logger.info("Tx submitted: %s", tx_hash_hex)

    logger.info("Waiting for receipt (up to 120 s) …")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status != 1:
        logger.error("Deployment transaction REVERTED. Hash: %s", tx_hash_hex)
        sys.exit(1)

    contract_address = receipt.contractAddress
    logger.info("✅  Contract deployed at: %s", contract_address)
    logger.info("    Block: %d  Gas used: %d", receipt.blockNumber, receipt.gasUsed)

    return {
        "contract_address": contract_address,
        "tx_hash": tx_hash_hex,
        "block_number": receipt.blockNumber,
        "gas_used": receipt.gasUsed,
        "chain_id": chain_id,
    }


# ---------------------------------------------------------------------------
# Polygonscan verification
# ---------------------------------------------------------------------------

def _verify_on_polygonscan(
    contract_address: str,
    sol_path: Path,
    contract_name: str,
    api_key: str,
    chain_id: int = POLYGON_CHAIN_ID,
) -> None:
    """Submit source code to Polygonscan for verification."""
    import requests

    api_url = "https://api.polygonscan.com/api"
    source = sol_path.read_text()

    payload = {
        "apikey":            api_key,
        "module":            "contract",
        "action":            "verifysourcecode",
        "contractaddress":   contract_address,
        "sourceCode":        source,
        "codeformat":        "solidity-single-file",
        "contractname":      contract_name,
        "compilerversion":   "v0.8.24+commit.e11b9ed9",
        "optimizationUsed":  "1",
        "runs":              "200",
        "constructorArguements": "",
        "licenseType":       "3",  # MIT
    }

    logger.info("Submitting source to Polygonscan …")
    resp = requests.post(api_url, data=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    logger.info("Polygonscan response: %s", result)

    if result.get("status") == "1":
        guid = result.get("result")
        logger.info("Verification submitted (GUID: %s). Polling …", guid)
        _poll_verification(api_url, api_key, guid)
    else:
        logger.warning("Polygonscan submission failed: %s", result.get("result"))


def _poll_verification(api_url: str, api_key: str, guid: str, retries: int = 12) -> None:
    """Poll Polygonscan until verification completes or times out."""
    import requests

    for attempt in range(retries):
        time.sleep(10)
        resp = requests.get(
            api_url,
            params={
                "apikey": api_key,
                "module": "contract",
                "action": "checkverifystatus",
                "guid": guid,
            },
            timeout=15,
        )
        result = resp.json()
        status = result.get("result", "")
        logger.info("Verification status [%d/%d]: %s", attempt + 1, retries, status)
        if "Pass" in status or "Already Verified" in status:
            logger.info("✅  Contract verified on Polygonscan.")
            return
        if "Fail" in status:
            logger.warning("❌  Verification failed: %s", status)
            return

    logger.warning("Verification polling timed out; check Polygonscan manually.")


# ---------------------------------------------------------------------------
# Artefact save
# ---------------------------------------------------------------------------

def _save_artefact(result: Dict[str, Any], contract_key: str, abi: str) -> Path:
    """Write deployment artefact JSON to /tmp for reference."""
    artefact = {
        **result,
        "contract_key": contract_key,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "abi": json.loads(abi),
    }
    out_path = Path("/tmp") / f"apex_{contract_key}_deployed.json"
    out_path.write_text(json.dumps(artefact, indent=2))
    logger.info("Artefact saved to %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy Apex-Omega executor contracts to Polygon."
    )
    parser.add_argument(
        "--contract",
        required=True,
        choices=list(CONTRACT_FILES),
        help="Which contract to deploy: 'institutional' or 'ultimate'.",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.getenv("POLYGON_RPC_URL") or os.getenv("APEX_RPC_URL"),
        help="Polygon HTTP-RPC URL. Defaults to POLYGON_RPC_URL env var.",
    )
    parser.add_argument(
        "--private-key",
        default=os.getenv("PRIVATE_KEY") or os.getenv("APEX_PRIVATE_KEY"),
        help="Deployer EOA private key (0x-prefixed). Defaults to PRIVATE_KEY env var.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the deployed contract on Polygonscan after deployment.",
    )
    parser.add_argument(
        "--polygonscan-key",
        default=os.getenv("POLYGONSCAN_API_KEY"),
        help="Polygonscan API key (required with --verify).",
    )
    parser.add_argument(
        "--gas-limit",
        type=int,
        default=4_000_000,
        help="Gas limit for the deployment transaction (default 4 000 000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile only — do not deploy or sign any transaction.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    sol_file = CONTRACTS_DIR / CONTRACT_FILES[args.contract]
    if not sol_file.exists():
        logger.error("Contract source not found: %s", sol_file)
        sys.exit(1)

    _ensure_solc("0.8.24")
    abi, bytecode = _compile_contract(sol_file)

    if args.dry_run:
        logger.info("Dry-run complete. Bytecode length: %d bytes.", len(bytecode) // 2)
        return

    if not args.rpc_url:
        logger.error(
            "No RPC URL provided. Pass --rpc-url or set POLYGON_RPC_URL."
        )
        sys.exit(1)
    if not args.private_key:
        logger.error(
            "No private key provided. Pass --private-key or set PRIVATE_KEY."
        )
        sys.exit(1)

    result = _deploy(
        abi=abi,
        bytecode=bytecode,
        rpc_url=args.rpc_url,
        private_key=args.private_key,
        gas_limit=args.gas_limit,
    )

    artefact_path = _save_artefact(result, args.contract, abi)

    if args.verify:
        if not args.polygonscan_key:
            logger.warning(
                "--verify requested but no Polygonscan API key found. "
                "Pass --polygonscan-key or set POLYGONSCAN_API_KEY."
            )
        else:
            _verify_on_polygonscan(
                contract_address=result["contract_address"],
                sol_path=sol_file,
                contract_name=sol_file.stem,
                api_key=args.polygonscan_key,
                chain_id=result["chain_id"],
            )

    print("\n" + "=" * 60)
    print(f"  Contract : {sol_file.stem}")
    print(f"  Address  : {result['contract_address']}")
    print(f"  Tx hash  : {result['tx_hash']}")
    print(f"  Block    : {result['block_number']}")
    print(f"  Gas used : {result['gas_used']}")
    print(f"  Artefact : {artefact_path}")
    print("=" * 60 + "\n")
    print("Next step: update contract_targets.py with the new address.")
    print(f"  C1_TARGET = \"{result['contract_address']}\"")


if __name__ == "__main__":
    main()
