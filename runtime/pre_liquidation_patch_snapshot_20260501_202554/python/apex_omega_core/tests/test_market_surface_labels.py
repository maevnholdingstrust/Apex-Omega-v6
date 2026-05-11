from apex_omega_core.core.market_surface_labels import classify_flash_ladder_zone, is_size_zone_allowed_for_c1


def test_zone_boundaries():
    assert classify_flash_ladder_zone(0.001) == "EXECUTABLE"
    assert classify_flash_ladder_zone(0.05) == "EXECUTABLE"
    assert classify_flash_ladder_zone(0.08) == "CONDITIONAL"
    assert classify_flash_ladder_zone(0.10) == "CONDITIONAL"
    assert classify_flash_ladder_zone(0.2) == "PROBE_ONLY"


def test_c1_allowed():
    assert is_size_zone_allowed_for_c1("EXECUTABLE")
    assert is_size_zone_allowed_for_c1("CONDITIONAL")
    assert not is_size_zone_allowed_for_c1("PROBE_ONLY")
