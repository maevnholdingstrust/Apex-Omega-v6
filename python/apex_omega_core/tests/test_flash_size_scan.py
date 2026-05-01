from dry_run import _flash_size_candidates_usd, _resolve_flash_loan_fee_rate


def test_flash_size_candidates_use_fractional_tvl_bounds():
    sizes = _flash_size_candidates_usd(
        weaker_pool_tvl_usd=100_000.0,
        min_flash_loan_usd=50.0,
        max_flash_loan_usd=20_000.0,
        max_trade_size_usd=15_000.0,
        max_flash_tvl_fraction=0.15,
        scan_fractions=[0.001, 0.01, 0.10, 0.20],
    )

    assert sizes == [50.0, 100.0, 1_000.0, 10_000.0, 15_000.0]


def test_flash_size_candidates_reject_when_pool_cannot_support_minimum():
    assert _flash_size_candidates_usd(
        weaker_pool_tvl_usd=100.0,
        min_flash_loan_usd=50.0,
        max_flash_loan_usd=20_000.0,
        max_trade_size_usd=15_000.0,
        max_flash_tvl_fraction=0.15,
        scan_fractions=[0.001, 0.01, 0.10],
    ) == []


def test_flash_loan_fee_bps_env_overrides_provider(monkeypatch):
    monkeypatch.setenv("FLASH_LOAN_FEE_BPS", "9")

    assert _resolve_flash_loan_fee_rate("balancer") == 0.0009


def test_flash_loan_fee_provider_defaults_are_decimal_rates(monkeypatch):
    monkeypatch.delenv("FLASH_LOAN_FEE_BPS", raising=False)

    assert _resolve_flash_loan_fee_rate("aave_v3") == 0.0009
    assert _resolve_flash_loan_fee_rate("balancer") == 0.0
