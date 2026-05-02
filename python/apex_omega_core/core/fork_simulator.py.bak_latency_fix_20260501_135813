from __future__ import annotations
import os, json, aiohttp
async def fork_healthcheck(fork_url=None, timeout_s=2.0):
    url = fork_url or os.getenv("FORK_RPC_URL", "http://127.0.0.1:8545")
    payload = {"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status < 200 or resp.status >= 300: return False
                return isinstance(json.loads(await resp.text()).get("result"), str)
    except Exception:
        return False
def anvil_command():
    rpc = os.getenv("POLYGON_RPC_URL") or os.getenv("ACTIVE_EXECUTION_RPC") or "<POLYGON_RPC_URL>"
    return f'anvil --fork-url "{rpc}" --chain-id 137 --host 127.0.0.1 --port 8545'
