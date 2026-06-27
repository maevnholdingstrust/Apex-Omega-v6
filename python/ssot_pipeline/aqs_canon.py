"""AQS canonical rule set v1.1.

This module is intentionally data-first. Architecture docs, audit reports,
dashboards, and package checks should read these rule definitions instead of
duplicating prose interpretations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]

ACTIVE_C2_DECISIONS = ("MIRROR", "REVERSE", "DO_NOTHING")
TERMINAL_C2_STATES = ("EXPIRED",)
TVL_GATE_USD = 5_000

SEVERITY_PENALTIES: dict[Severity, int] = {
    "CRITICAL": -15,
    "HIGH": -8,
    "MEDIUM": -3,
    "LOW": -1,
}


@dataclass(frozen=True)
class AqsRule:
    rule_id: str
    name: str
    severity: Severity
    requirement: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AqsViolation:
    rule_id: str
    rule_name: str
    violation_location: str
    evidence: str
    severity: Severity
    impact: str
    required_remediation: str
    schema_changes: str = "None"
    code_changes: str = "None"
    documentation_changes: str = "None"
    deployment_changes: str = "None"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


RULES: tuple[AqsRule, ...] = (
    AqsRule("AQS-001", "Opportunity Is The Parent Object", "CRITICAL", "C1 and C2 may only exist under an Opportunity."),
    AqsRule("AQS-002", "C1 And C2 Are Execution Cycles", "CRITICAL", "C1 and C2 are execution cycles, not swap legs."),
    AqsRule("AQS-003", "LEG1 And LEG2 Are Swap Legs", "CRITICAL", "LEG1 and LEG2 exist only inside execution cycles."),
    AqsRule("AQS-004", "C1 Is Atomic", "CRITICAL", "C1 performs validation, allocation, LEG1, LEG2, repayment, settlement, profit realization, and state commitment."),
    AqsRule("AQS-005", "C1 Independent Profitability", "CRITICAL", "C1_NET_PROFIT > C1_MIN_PROFIT_THRESHOLD."),
    AqsRule("AQS-006", "C1 Cannot Be A Loss-Leader", "CRITICAL", "C1_NET must not be negative while expected opportunity profit is positive."),
    AqsRule("AQS-007", "C2 Is Independent", "CRITICAL", "C2 is a separate execution cycle and must not share execution state with C1."),
    AqsRule("AQS-008", "C2 Requires C1 Confirmation Hash", "CRITICAL", "VALID_C1_CONFIRMATION_HASH must be true before C2 evaluation."),
    AqsRule("AQS-009", "C2 Window", "CRITICAL", "CURRENT_BLOCK - C1_BLOCK <= 5."),
    AqsRule("AQS-010", "C2 Decision Model", "CRITICAL", "Active decisions are MIRROR, REVERSE, DO_NOTHING; terminal state is EXPIRED."),
    AqsRule("AQS-011", "C2 Independent Profitability", "CRITICAL", "C2_NET_PROFIT > C2_MIN_PROFIT_THRESHOLD when C2 executes."),
    AqsRule("AQS-012", "C2 Cannot Subsidize C1", "CRITICAL", "C2 profit must not mask a negative C1 when opportunity net is positive."),
    AqsRule("AQS-013", "Price Invariant", "CRITICAL", "LEG1_EXECUTABLE_PRICE < LEG2_EXECUTABLE_PRICE."),
    AqsRule("AQS-014", "Liquidity Monitor", "CRITICAL", "Liquidity monitor must approve TVL, depth, route capacity, slippage, and capital safety before injection."),
    AqsRule("AQS-015", "TVL Gate", "HIGH", "TVL_GATE_USD == 5000 unless an official config override is present."),
    AqsRule("AQS-016", "Opportunity Profit Equation", "CRITICAL", "OPPORTUNITY_PROFIT = C1_PROFIT + C2_PROFIT_IF_EXECUTED."),
    AqsRule("AQS-017", "Standalone Accretion Rule", "CRITICAL", "C1, C2, and liquidation cycles execute only when independently accretive."),
    AqsRule("AQS-018", "C1 Confirmation Hash Emission", "CRITICAL", "C1 settlement must emit opportunity/execution IDs, hashes, realized profit, and block number."),
    AqsRule("AQS-019", "Deterministic Route Hash", "CRITICAL", "Route hash inputs must include opportunity ID, execution type, asset, amount, path, calldata hash, min-outs, deadline, and recipient."),
    AqsRule("AQS-020", "Deterministic Opportunity ID", "CRITICAL", "Opportunity IDs use chain, tokens, venues, block, and initial state hash; timestamp-only IDs are forbidden."),
    AqsRule("AQS-021", "Operating Expense Separation", "HIGH", "Bridge/infrastructure costs must live in OPERATING_EXPENSE_LEDGER, not execution-cycle PnL."),
    AqsRule("AQS-022", "Rejected Opportunity Accounting", "HIGH", "Rejected opportunities are tracked separately and do not affect execution or opportunity PnL."),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}


def classify_c2_state(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized in ACTIVE_C2_DECISIONS:
        return "ACTIVE_DECISION"
    if normalized in TERMINAL_C2_STATES:
        return "TERMINAL_STATE"
    return "INVALID"


def opportunity_profit(c1_profit: float, c2_profit: float | None = None, *, c2_executed: bool = False) -> float:
    """Return dollar PnL for an Opportunity.

    Profit is additive across independent execution cycles. Multiplying C1 and
    C2 profits would produce squared-dollar units and is rejected by this canon.
    """
    return float(c1_profit) + (float(c2_profit or 0.0) if c2_executed else 0.0)


def score_compliance(violations: Iterable[AqsViolation]) -> dict[str, object]:
    rows = list(violations)
    score = max(0, 100 + sum(SEVERITY_PENALTIES[row.severity] for row in rows))
    critical_count = sum(1 for row in rows if row.severity == "CRITICAL")
    if score == 100:
        band = "Fully Canonical"
    elif score >= 90:
        band = "Minor Remediation"
    elif score >= 75:
        band = "Significant Remediation"
    elif score >= 50:
        band = "Major Compliance Failure"
    else:
        band = "Critical Architectural Failure"
    return {
        "score": score,
        "band": band,
        "critical_violations": critical_count,
        "certification_denied": critical_count > 0,
        "violations": [row.as_dict() for row in rows],
    }


def canonical_summary() -> dict[str, object]:
    return {
        "version": "AQS_CANONICAL_RULE_SET_V1_1",
        "active_c2_decisions": list(ACTIVE_C2_DECISIONS),
        "terminal_c2_states": list(TERMINAL_C2_STATES),
        "tvl_gate_usd": TVL_GATE_USD,
        "opportunity_profit_equation": "OPPORTUNITY_PROFIT = C1_PROFIT + C2_PROFIT_IF_EXECUTED",
        "rule_count": len(RULES),
        "rules": [rule.as_dict() for rule in RULES],
        "severity_penalties": dict(SEVERITY_PENALTIES),
    }
