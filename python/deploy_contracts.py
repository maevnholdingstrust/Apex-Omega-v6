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
from typing import Any, Dict, Tuple

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
    "apex_vm":       "ApexOmegaExecutionVM.sol",
    "apex_vm_split": "ApexOmegaExecutionVMSplit.sol",
    "apex_vm_saas":  "ApexOmegaExecutionVMSplit.sol",
    "liquidation":   "LiquidationExecutor.sol",
    "liquidation_split": "LiquidationExecutorSplit.sol",
    "liquidation_saas":  "LiquidationExecutorSplit.sol",
}

# Polygon mainnet chain ID
POLYGON_CHAIN_ID = 137


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / "python" / "apex_omega_core" / ".env", override=False)

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


def _compile_contract(sol_path: Path, solc_version: str = "0.8.24") -> Tuple[str, str]:
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

    oz_path = REPO_ROOT / "node_modules" / "@openzeppelin"
    remappings = []
    if oz_path.exists():
        remappings.append(f"@openzeppelin/={oz_path.resolve().as_posix()}/")

    standard_input = {
        "language": "Solidity",
        "sources": {f"contracts/{sol_path.name}": {"content": source}},
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "remappings": remappings,
            "outputSelection": {
                "*": {
                    "*": ["abi", "evm.bytecode.object"],
                }
            },
        },
    }
    result = solcx.compile_standard(
        standard_input,
        solc_version=solc_version,
        allow_paths=str(REPO_ROOT),
    )

    stem = sol_path.stem
    compiled_contracts = result["contracts"][f"contracts/{sol_path.name}"]
    key = stem if stem in compiled_contracts else next(iter(compiled_contracts))
    contract_data = compiled_contracts[key]
    abi = json.dumps(contract_data["abi"])
    bytecode = contract_data["evm"]["bytecode"]["object"]
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
    constructor_args: list[Any] | None = None,
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

    constructor_args = constructor_args or []
    deploy_tx = contract.constructor(*constructor_args).build_transaction({
        "chainId": chain_id,
        "from": account.address,
        "nonce": nonce,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": tip,
    })

    signed = w3.eth.account.sign_transaction(deploy_tx, private_key=private_key)
    logger.info("Broadcasting deployment transaction …")
    raw_transaction = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw_transaction)
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
    constructor_arguments: str = "",
    compiler_version: str = "0.8.24",
) -> None:
    """Submit source code to Polygonscan for verification."""
    import requests

    api_url = os.getenv("ETHERSCAN_API_URL", "https://api.etherscan.io/v2/api")
    post_url = api_url
    post_params = None
    if "api.etherscan.io/v2/api" in api_url and "chainid=" not in api_url:
        # Etherscan V2 verification accepts chainid reliably as a query param.
        # Some verification actions reject it when sent only in the POST body.
        post_params = {"chainid": str(chain_id)}
    source = sol_path.read_text()

    payload = {
        "apikey":            api_key,
        "module":            "contract",
        "action":            "verifysourcecode",
        "contractaddress":   contract_address,
        "sourceCode":        source,
        "codeformat":        "solidity-single-file",
        "contractname":      contract_name,
        "compilerversion":   _etherscan_compiler_version(compiler_version),
        "optimizationUsed":  "1",
        "runs":              "200",
        "constructorArguments": constructor_arguments,
        "licenseType":       "3",  # MIT
    }

    logger.info("Submitting source to Polygonscan …")
    resp = requests.post(post_url, params=post_params, data=payload, timeout=30)
    resp.raise_for_status()
    try:
        result = resp.json()
    except ValueError:
        logger.warning("Explorer verification returned non-JSON response: HTTP %s %s", resp.status_code, resp.text[:300])
        return
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
                "chainid": str(POLYGON_CHAIN_ID),
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


def _etherscan_compiler_version(version: str) -> str:
    commits = {
        "0.8.20": "a1b79de6",
        "0.8.21": "d9974bed",
        "0.8.22": "4fc1097e",
        "0.8.23": "f704f362",
        "0.8.24": "e11b9ed9",
        "0.8.25": "b61c2a91",
        "0.8.26": "8a97fa7a",
    }
    commit = commits.get(version)
    if not commit:
        raise ValueError(f"Unsupported compiler version for verification: {version}")
    return f"v{version}+commit.{commit}"


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


def _apex_vm_constructor_args(args: argparse.Namespace) -> list[str]:
    if args.contract not in {"apex_vm", "apex_vm_split", "apex_vm_saas"}:
        return []
    aave_pool = args.aave_v3_pool or os.getenv("AAVE_V3_POOL_ADDRESS") or os.getenv("AAVE_V3_POOL")
    balancer_v2_vault = (
        args.balancer_v2_vault
        or args.balancer_vault
        or os.getenv("BALANCER_VAULT_ADDRESS")
        or os.getenv("BALANCER_VAULT")
        or os.getenv("BALANCER_V2_VAULT")
    )
    balancer_v3_vault = (
        args.balancer_v3_vault
        or os.getenv("BALANCER_V3_VAULT_ADDRESS")
        or os.getenv("BALANCER_V3_VAULT")
        or "0xbA1333333333a1BA1108E8412f11850A5C319bA9"
    )
    if not aave_pool:
        logger.error("apex_vm deployment requires --aave-v3-pool or AAVE_V3_POOL_ADDRESS.")
        sys.exit(1)
    if not balancer_v2_vault:
        logger.error("apex_vm deployment requires --balancer-v2-vault or BALANCER_VAULT_ADDRESS.")
        sys.exit(1)
    if not balancer_v3_vault:
        logger.error("apex_vm deployment requires --balancer-v3-vault or BALANCER_V3_VAULT_ADDRESS.")
        sys.exit(1)
    constructor_args = [aave_pool, balancer_v2_vault, balancer_v3_vault]
    if args.contract in {"apex_vm_split", "apex_vm_saas"}:
        treasury = (
            args.platform_treasury
            or os.getenv("SAAS_TREASURY_ADDRESS")
            or os.getenv("PLATFORM_TREASURY_ADDRESS")
        )
        if not treasury:
            logger.error("apex_vm_saas deployment requires --platform-treasury or SAAS_TREASURY_ADDRESS.")
            sys.exit(1)
        constructor_args.append(treasury)
    return constructor_args


def _liquidation_constructor_args(args: argparse.Namespace) -> list[str]:
    if args.contract not in {"liquidation", "liquidation_split", "liquidation_saas"}:
        return []
    receiver = (
        args.profit_receiver
        or os.getenv("LIQUIDATION_PROFIT_RECEIVER")
        or os.getenv("EXECUTOR_WALLET_ADDRESS")
        or os.getenv("OWNER_ADDRESS")
        or os.getenv("OPERATOR_ADDRESS")
    )
    if not receiver:
        logger.error("liquidation deployment requires --profit-receiver or LIQUIDATION_PROFIT_RECEIVER/EXECUTOR_WALLET_ADDRESS.")
        sys.exit(1)
    constructor_args = [receiver]
    if args.contract in {"liquidation_split", "liquidation_saas"}:
        treasury = (
            args.platform_treasury
            or os.getenv("SAAS_TREASURY_ADDRESS")
            or os.getenv("PLATFORM_TREASURY_ADDRESS")
        )
        if not treasury:
            logger.error("liquidation_saas deployment requires --platform-treasury or SAAS_TREASURY_ADDRESS.")
            sys.exit(1)
        constructor_args.append(treasury)
    return constructor_args


def _constructor_args(contract_key: str, args: argparse.Namespace) -> list[Any]:
    if contract_key in {"apex_vm", "apex_vm_split", "apex_vm_saas"}:
        return _apex_vm_constructor_args(args)
    if contract_key in {"liquidation", "liquidation_split", "liquidation_saas"}:
        return _liquidation_constructor_args(args)
    return []


def _constructor_args_hex(contract_key: str, constructor_args: list[Any]) -> str:
    if not constructor_args:
        return ""
    try:
        from eth_abi import encode
    except ImportError:
        logger.warning("eth_abi not installed; Polygonscan constructor args will be empty.")
        return ""
    if contract_key == "apex_vm":
        return encode(["address", "address", "address"], constructor_args).hex()
    if contract_key in {"apex_vm_split", "apex_vm_saas"}:
        return encode(["address", "address", "address", "address"], constructor_args).hex()
    if contract_key == "liquidation":
        return encode(["address"], constructor_args).hex()
    if contract_key in {"liquidation_split", "liquidation_saas"}:
        return encode(["address", "address"], constructor_args).hex()
    return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    _load_dotenv_files()
    parser = argparse.ArgumentParser(
        description="Deploy Apex-Omega executor contracts to Polygon."
    )
    parser.add_argument(
        "--contract",
        required=True,
        choices=list(CONTRACT_FILES),
        help="Which contract to deploy.",
    )
    parser.add_argument(
        "--solc-version",
        default=os.getenv("SOLC_VERSION", "0.8.24"),
        help="Solidity compiler version for compile/deploy/verify.",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.getenv("POLYGON_RPC_URL") or os.getenv("POLYGON_RPC") or os.getenv("APEX_RPC_URL"),
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
    parser.add_argument(
        "--aave-v3-pool",
        default=os.getenv("AAVE_V3_POOL_ADDRESS") or os.getenv("AAVE_V3_POOL"),
        help="Aave V3 Pool constructor arg for --contract apex_vm.",
    )
    parser.add_argument(
        "--balancer-vault",
        default=os.getenv("BALANCER_VAULT_ADDRESS") or os.getenv("BALANCER_VAULT") or os.getenv("BALANCER_V2_VAULT"),
        help="Alias for --balancer-v2-vault.",
    )
    parser.add_argument(
        "--balancer-v2-vault",
        default=os.getenv("BALANCER_VAULT_ADDRESS") or os.getenv("BALANCER_VAULT") or os.getenv("BALANCER_V2_VAULT"),
        help="Balancer V2 Vault constructor arg for --contract apex_vm.",
    )
    parser.add_argument(
        "--balancer-v3-vault",
        default=os.getenv("BALANCER_V3_VAULT_ADDRESS") or os.getenv("BALANCER_V3_VAULT") or "0xbA1333333333a1BA1108E8412f11850A5C319bA9",
        help="Balancer V3 Vault constructor arg for --contract apex_vm/apex_vm_split.",
    )
    parser.add_argument(
        "--platform-treasury",
        default=os.getenv("SAAS_TREASURY_ADDRESS") or os.getenv("PLATFORM_TREASURY_ADDRESS"),
        help="70 percent profit receiver constructor arg for --contract apex_vm_saas/liquidation_saas.",
    )
    parser.add_argument(
        "--profit-receiver",
        default=os.getenv("LIQUIDATION_PROFIT_RECEIVER") or os.getenv("EXECUTOR_WALLET_ADDRESS") or os.getenv("OWNER_ADDRESS") or os.getenv("OPERATOR_ADDRESS"),
        help="Profit receiver constructor arg for --contract liquidation.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    sol_file = CONTRACTS_DIR / CONTRACT_FILES[args.contract]
    if not sol_file.exists():
        logger.error("Contract source not found: %s", sol_file)
        sys.exit(1)

    _ensure_solc(args.solc_version)
    abi, bytecode = _compile_contract(sol_file, args.solc_version)
    constructor_args = _constructor_args(args.contract, args)
    constructor_args_hex = _constructor_args_hex(args.contract, constructor_args)

    if args.dry_run:
        logger.info("Dry-run complete. Bytecode length: %d bytes.", len(bytecode) // 2)
        if constructor_args:
            logger.info("Constructor args: %s", constructor_args)
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
        constructor_args=constructor_args,
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
                constructor_arguments=constructor_args_hex,
                compiler_version=args.solc_version,
            )

    print("\n" + "=" * 60)
    print(f"  Contract : {sol_file.stem}")
    print(f"  Address  : {result['contract_address']}")
    print(f"  Tx hash  : {result['tx_hash']}")
    print(f"  Block    : {result['block_number']}")
    print(f"  Gas used : {result['gas_used']}")
    print(f"  Artefact : {artefact_path}")
    print("=" * 60 + "\n")
    if args.contract in {"apex_vm_split", "apex_vm_saas"}:
        print("Next step: update SaaS target fields with the new split VM address.")
        print(f"  EXECUTOR_ADDRESS_SAAS = \"{result['contract_address']}\"")
        print(f"  C1_TARGET_SAAS = \"{result['contract_address']}\"")
        print(f"  C2_TARGET_SAAS = \"{result['contract_address']}\"")
    elif args.contract == "apex_vm":
        print("Next step: update contract_targets.py with the new VM address.")
        print(f"  C1_TARGET = \"{result['contract_address']}\"")
        print(f"  C2_TARGET = \"{result['contract_address']}\"")
    elif args.contract in {"liquidation_split", "liquidation_saas"}:
        print("Next step: update SaaS liquidation target field with the new split liquidation address.")
        print(f"  LIQUIDATION_EXECUTOR_ADDRESS_SAAS = \"{result['contract_address']}\"")
    elif args.contract == "liquidation":
        print("Next step: update contract_targets.py with the new liquidation address.")
        print(f"  LIQUIDATION_EXECUTOR_ADDRESS = \"{result['contract_address']}\"")
    else:
        print("Next step: update contract_targets.py with the new address.")
        print(f"  C1_TARGET = \"{result['contract_address']}\"")


if __name__ == "__main__":
    main()
