import json

import pytest

from apex_omega_core.core import onchain_v2_discovery


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._body = json.dumps(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def post(self, url, **_kwargs):
        self.urls.append(url)
        return self.responses.pop(0)


def test_env_rpc_urls_dedupes_configured_and_discovered(monkeypatch):
    for name in (
        "ACTIVE_DISCOVERY_RPC",
        "ACTIVE_EXECUTION_RPC",
        "POLYGON_RPC_URL",
        "WEB3_PROVIDER_URI",
        "PRIVATE_RPC_URL",
        "POLYGON_RPC",
        "PUBLIC_DRPC",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ACTIVE_DISCOVERY_RPC", "https://primary.example")
    monkeypatch.setenv("POLYGON_RPC_URL", "https://primary.example")
    monkeypatch.setenv("PUBLIC_DRPC", "https://public.example")
    monkeypatch.setattr(
        onchain_v2_discovery,
        "discover_public_rpc_urls",
        lambda _chain_id: ["https://public.example", "https://fallback.example"],
    )

    assert onchain_v2_discovery._env_rpc_urls() == [
        "https://primary.example",
        "https://public.example",
        "https://fallback.example",
    ]


@pytest.mark.asyncio
async def test_rpc_client_retries_eth_call_after_rate_limit():
    session = _Session(
        [
            _Response(429, {"error": {"message": "rate limited"}}),
            _Response(200, {"result": "0x1234"}),
        ]
    )
    client = onchain_v2_discovery.RpcClient(
        ["https://limited.example", "https://healthy.example"]
    )

    result = await client.call(session, "0x" + "1" * 40, "0x12345678")

    assert result == "0x1234"
    assert session.urls == [
        "https://limited.example",
        "https://healthy.example",
    ]


@pytest.mark.asyncio
async def test_rpc_client_retries_block_number_after_rpc_error():
    session = _Session(
        [
            _Response(200, {"error": {"code": 429}}),
            _Response(200, {"result": "0x89"}),
        ]
    )
    client = onchain_v2_discovery.RpcClient(
        ["https://limited.example", "https://healthy.example"]
    )

    assert await client.block_number(session) == 137
