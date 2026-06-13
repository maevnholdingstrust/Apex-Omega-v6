from apex_omega_core.core.execution_transport_selector import select_execution_transport


def test_polygon_submission_requires_private_mempool_url(monkeypatch):
    monkeypatch.delenv("POLYGON_PRIVATE_MEMPOOL_RPC_URL", raising=False)
    monkeypatch.delenv("POLYGON_PRIVATE_MEMPOOL_URL", raising=False)
    monkeypatch.delenv("TITAN_MEV_US_WEST", raising=False)
    monkeypatch.setenv("ACTIVE_EXECUTION_RPC", "https://public-read.example")

    transport = select_execution_transport(chain_id=137)

    assert transport.mode == "none"
    assert transport.url == ""


def test_polygon_submission_uses_only_private_mempool_url(monkeypatch):
    monkeypatch.setenv("POLYGON_PRIVATE_MEMPOOL_RPC_URL", "https://private-submit.example")
    monkeypatch.setenv("ACTIVE_PRIVATE_RELAY", "https://rpc.titanbuilder.xyz")
    monkeypatch.setenv("ACTIVE_EXECUTION_RPC", "https://public-read.example")

    transport = select_execution_transport(chain_id=137)

    assert transport.mode == "polygon_private_mempool"
    assert transport.url == "https://private-submit.example"


def test_polygon_submission_can_use_titan_builder_bundle_lane(monkeypatch):
    monkeypatch.delenv("POLYGON_PRIVATE_MEMPOOL_RPC_URL", raising=False)
    monkeypatch.delenv("POLYGON_PRIVATE_MEMPOOL_URL", raising=False)
    monkeypatch.setenv("TITAN_MEV_US_WEST", "https://us.rpc.titanbuilder.xyz")
    monkeypatch.setenv("ACTIVE_EXECUTION_RPC", "https://public-read.example")

    transport = select_execution_transport(chain_id=137)

    assert transport.mode == "titan_builder_bundle"
    assert transport.url == "https://us.rpc.titanbuilder.xyz"


def test_polygon_private_mempool_takes_precedence_over_titan(monkeypatch):
    monkeypatch.setenv("POLYGON_PRIVATE_MEMPOOL_RPC_URL", "https://private-submit.example")
    monkeypatch.setenv("TITAN_MEV_US_WEST", "https://us.rpc.titanbuilder.xyz")

    transport = select_execution_transport(chain_id=137)

    assert transport.mode == "polygon_private_mempool"
    assert transport.url == "https://private-submit.example"
