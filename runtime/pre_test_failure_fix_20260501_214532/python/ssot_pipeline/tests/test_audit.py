"""Tests for ssot_pipeline.audit.

Coverage:
  - audit_two_leg_route_envelope: valid plan passes, each individual violation
    is detected (inventory drift, p_gross mismatch, p_net mismatch, fee range).
"""

from ssot_pipeline.audit import audit_two_leg_route_envelope
from ssot_pipeline.types import RouteAuditResult


def _valid_kwargs(**overrides):
    """Return kwargs for a well-formed 2-leg plan."""
    a_in = 1000.0
    b_out_1 = 995.0
    a_out_2 = 1008.5
    p_gross = a_out_2 - a_in   # = 8.5
    c_total = 1.0
    p_net = p_gross
    base = dict(
        a_in=a_in,
        fee1=0.003,
        b_out_1=b_out_1,
        b_in_2=b_out_1,
        fee2=0.0025,
        a_out_2=a_out_2,
        p_gross=p_gross,
        p_net=p_net,
        c_total=c_total,
    )
    base.update(overrides)
    return base


class TestAuditTwoLegRouteEnvelope:
    def test_valid_plan_passes(self):
        result = audit_two_leg_route_envelope(**_valid_kwargs())
        assert isinstance(result, RouteAuditResult)
        assert result.passed is True
        assert result.violations == []

    def test_inventory_drift_detected(self):
        """b_in_2 != b_out_1 must produce an inventory_drift violation."""
        result = audit_two_leg_route_envelope(**_valid_kwargs(b_in_2=900.0))
        assert result.passed is False
        assert any("inventory_drift" in v for v in result.violations)

    def test_p_gross_mismatch_detected(self):
        """p_gross that does not equal a_out_2 - a_in must be flagged."""
        result = audit_two_leg_route_envelope(**_valid_kwargs(p_gross=9999.0))
        assert result.passed is False
        assert any("p_gross_mismatch" in v for v in result.violations)

    def test_p_net_mismatch_detected(self):
        """p_net that does not equal route token p_gross must be flagged."""
        result = audit_two_leg_route_envelope(**_valid_kwargs(p_net=9999.0))
        assert result.passed is False
        assert any("p_net_mismatch" in v for v in result.violations)

    def test_fee1_below_zero_detected(self):
        result = audit_two_leg_route_envelope(**_valid_kwargs(fee1=-0.001))
        assert result.passed is False
        assert any("fee1_range" in v for v in result.violations)

    def test_fee1_at_one_detected(self):
        result = audit_two_leg_route_envelope(**_valid_kwargs(fee1=1.0))
        assert result.passed is False
        assert any("fee1_range" in v for v in result.violations)

    def test_fee2_below_zero_detected(self):
        result = audit_two_leg_route_envelope(**_valid_kwargs(fee2=-0.001))
        assert result.passed is False
        assert any("fee2_range" in v for v in result.violations)

    def test_fee2_at_one_detected(self):
        result = audit_two_leg_route_envelope(**_valid_kwargs(fee2=1.0))
        assert result.passed is False
        assert any("fee2_range" in v for v in result.violations)

    def test_multiple_violations_all_reported(self):
        """All independent violations should be collected, not short-circuited."""
        result = audit_two_leg_route_envelope(**_valid_kwargs(
            b_in_2=1.0,    # inventory drift
            p_gross=9999.0,  # p_gross mismatch (also causes p_net mismatch)
            fee1=-0.1,     # fee1 out of range
        ))
        assert result.passed is False
        assert len(result.violations) >= 3

    def test_tolerance_respected(self):
        """Differences within tolerance must not produce violations."""
        kw = _valid_kwargs()
        # Introduce a sub-tolerance delta to b_in_2
        kw["b_in_2"] = kw["b_out_1"] + 1e-12
        result = audit_two_leg_route_envelope(**kw, tolerance=1e-9)
        assert result.passed is True

    def test_tolerance_violation_at_boundary(self):
        """Differences just above tolerance must produce a violation."""
        kw = _valid_kwargs()
        kw["b_in_2"] = kw["b_out_1"] + 1e-8  # > 1e-9 tolerance
        result = audit_two_leg_route_envelope(**kw, tolerance=1e-9)
        assert result.passed is False
        assert any("inventory_drift" in v for v in result.violations)
