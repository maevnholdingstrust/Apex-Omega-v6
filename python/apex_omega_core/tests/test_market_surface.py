from apex_omega_core.core.market_surface import scan_market_surface


def test_market_surface_scan_runs():
    ops = scan_market_surface(top_n=3)
    assert isinstance(ops, tuple)


def test_market_distance_structure():
    ops = scan_market_surface(top_n=1)
    if not ops:
        return
    o = ops[0]
    assert o.best_buy is not None
    assert o.best_sell is not None
    assert hasattr(o, "size_ladder")


def test_size_ladder_net_profit_field():
    ops = scan_market_surface(top_n=1)
    if not ops:
        return
    ladder = ops[0].size_ladder
    for p in ladder:
        assert hasattr(p, "net_profit_usd")
