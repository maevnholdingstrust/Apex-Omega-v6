from ssot_pipeline.aqs_canon import (
    ACTIVE_C2_DECISIONS,
    RULES,
    TERMINAL_C2_STATES,
    AqsViolation,
    canonical_summary,
    classify_c2_state,
    opportunity_profit,
    score_compliance,
)


def test_c2_state_model_separates_active_and_terminal_states():
    assert ACTIVE_C2_DECISIONS == ("MIRROR", "REVERSE", "DO_NOTHING")
    assert TERMINAL_C2_STATES == ("EXPIRED",)
    assert classify_c2_state("MIRROR") == "ACTIVE_DECISION"
    assert classify_c2_state("REVERSE") == "ACTIVE_DECISION"
    assert classify_c2_state("DO_NOTHING") == "ACTIVE_DECISION"
    assert classify_c2_state("EXPIRED") == "TERMINAL_STATE"
    assert classify_c2_state("STRIKE") == "INVALID"


def test_aqs_rule_set_contains_all_22_rules():
    summary = canonical_summary()
    assert summary["version"] == "AQS_CANONICAL_RULE_SET_V1_1"
    assert summary["rule_count"] == 22
    assert len(RULES) == 22


def test_opportunity_profit_is_unit_correct_addition_not_multiplication():
    assert opportunity_profit(10.0, 4.0, c2_executed=True) == 14.0
    assert opportunity_profit(10.0, 4.0, c2_executed=False) == 10.0
    assert opportunity_profit(10.0, 4.0, c2_executed=True) != 40.0


def test_compliance_score_denies_certification_on_critical_violation():
    result = score_compliance(
        [
            AqsViolation(
                rule_id="AQS-010",
                rule_name="C2 Decision Model",
                violation_location="test",
                evidence="STRIKE",
                severity="CRITICAL",
                impact="Invalid C2 state",
                required_remediation="Use MIRROR, REVERSE, DO_NOTHING, or EXPIRED.",
            )
        ]
    )

    assert result["score"] == 85
    assert result["critical_violations"] == 1
    assert result["certification_denied"] is True
