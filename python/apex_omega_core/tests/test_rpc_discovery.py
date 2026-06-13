import io
import json

from apex_omega_core.core import rpc_discovery


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_discovery_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DODO_RPC_DISCOVERY_ENABLED", raising=False)
    assert rpc_discovery.discover_public_rpc_urls(137) == []


def test_discovery_filters_and_caches_public_http_urls(monkeypatch):
    rpc_discovery._CACHE.clear()
    monkeypatch.setenv("DODO_RPC_DISCOVERY_ENABLED", "true")
    requests = []

    def _urlopen(url, timeout):
        requests.append((url, timeout))
        payload = [
            {"chainId": 137, "url": "https://polygon.example"},
            {"chainId": 137, "url": "https://polygon.example"},
            {"chainId": 137, "url": "wss://polygon.example"},
            {"chainId": 137, "url": "https://${API_KEY}.example"},
            {"chainId": 1, "url": "https://ethereum.example"},
        ]
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(rpc_discovery.urllib.request, "urlopen", _urlopen)

    assert rpc_discovery.discover_public_rpc_urls(137) == ["https://polygon.example"]
    assert rpc_discovery.discover_public_rpc_urls(137) == ["https://polygon.example"]
    assert len(requests) == 1
    assert "sources%5B%5D=ChainList" in requests[0][0]
