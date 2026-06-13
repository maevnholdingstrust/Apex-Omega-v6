import pytest

from apex_omega_core.core.relay_submitter import RelayBundleSubmitter


def test_titan_bundle_payload_uses_documented_eth_send_bundle_shape():
    payload = RelayBundleSubmitter.build_titan_bundle_payload(
        ["deadbeef"],
        target_block=88_195_900,
        replacement_uuid="c1-cycle-001",
    )

    assert payload["method"] == "eth_sendBundle"
    assert payload["params"] == [
        {
            "txs": ["0xdeadbeef"],
            "blockNumber": hex(88_195_900),
            "replacementUuid": "c1-cycle-001",
        }
    ]


def test_titan_private_transaction_payload_uses_documented_shape():
    payload = RelayBundleSubmitter.build_titan_private_transaction_payload("deadbeef")

    assert payload["method"] == "eth_sendPrivateTransaction"
    assert payload["params"] == [{"tx": "0xdeadbeef"}]


def test_titan_private_transaction_dry_run_requires_single_tx():
    submitter = RelayBundleSubmitter(config=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="exactly one raw transaction"):
        submitter.dry_run_titan_payload(
            ["0xaaa", "0xbbb"],
            target_block=88_195_900,
            private_transaction=True,
        )
