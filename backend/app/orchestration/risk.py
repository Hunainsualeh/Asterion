"""Risk-scored autonomy — doc Section 08, implemented against real history.

Every ticket reaching the end of Build/Review/Test gets a computed risk score
from three signals: the category's inherent blast radius, its actual failure
history in the Knowledge Store ledger, and what this run's security/test
stages found. The score maps to the three tiers:

  Tier 0 — ships on its own, audit trail only (docs/config-grade changes)
  Tier 1 — auto-proceeds after checks pass, human gets an async digest
  Tier 2 — hard stop, explicit human decision (current MANUAL_TEST gate)

Guardrails, per the doc: autonomy is only *eligible* for explicitly
allowlisted categories (expanding that list is a human decision — edit
AUTONOMY_ELIGIBLE, nothing expands itself), and it degrades automatically:
any category failing ≥30% of its recent outcomes is forced back to Tier 2
regardless of score.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.knowledge import store
from app.knowledge.classify import categorize

log = logging.getLogger("asterion.risk")

# Inherent blast radius per category, 0-100.
BASE_RISK: dict[str, int] = {
    "security": 90,
    "payments": 90,
    "schema": 80,
    "infra": 60,
    "api": 50,
    "logic": 40,
    "ui": 30,
    "tests": 20,
    "config": 15,
    "docs": 5,
}
DEFAULT_BASE_RISK = 60  # unknown category = treat as risky, never as safe

# Categories a human has signed off as eligible for any autonomy at all.
# Expanding this set is itself a Tier-2 action: a human edits it, the system
# never adds to it. Everything outside it hard-stops at the gate.
AUTONOMY_ELIGIBLE: frozenset[str] = frozenset({"docs", "config", "tests", "ui"})

TIER0_MAX_SCORE = 20
TIER1_MAX_SCORE = 45

# Auto-downgrade: a category failing at/above this rate over its recent
# window loses autonomy immediately, whatever its base score says.
DOWNGRADE_FAILURE_RATE = 0.30
HISTORY_WINDOW = 10
MIN_HISTORY_FOR_TRUST = 3
UNKNOWN_HISTORY_PENALTY = 15


@dataclass
class RiskAssessment:
    tier: int              # 0 | 1 | 2
    score: int
    category: str
    reasons: list[str] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {"tier": self.tier, "score": self.score, "category": self.category, "reasons": self.reasons}


async def assess_ticket(
    ticket: dict,
    security_blocking: int = 0,
    security_advisory: int = 0,
    tests_failed: bool = False,
) -> RiskAssessment:
    category = categorize(ticket)
    score = BASE_RISK.get(category, DEFAULT_BASE_RISK)
    reasons = [f"base risk for '{category}' = {score}"]

    # This run's own signals trump everything.
    if security_blocking:
        return RiskAssessment(2, 100, category, [f"{security_blocking} blocking security finding(s) — hard stop"])
    if tests_failed:
        return RiskAssessment(2, 100, category, ["automated tests still failing — hard stop"])
    if security_advisory:
        score += 10
        reasons.append(f"+10: {security_advisory} advisory security finding(s)")

    # Historical failure rate from the outcome ledger.
    try:
        fails, total = await store.failure_rate(category, window=HISTORY_WINDOW)
    except Exception:  # noqa: BLE001 - a broken ledger must fail toward caution, not crash
        log.exception("failure_rate lookup failed; treating history as unknown")
        fails, total = 0, 0

    unproven = total < MIN_HISTORY_FOR_TRUST
    if unproven:
        score += UNKNOWN_HISTORY_PENALTY
        reasons.append(f"+{UNKNOWN_HISTORY_PENALTY}: only {total} recorded outcome(s) in this category")
    else:
        rate = fails / total
        if rate >= DOWNGRADE_FAILURE_RATE:
            return RiskAssessment(
                2, max(score, 80), category,
                reasons + [f"auto-downgrade: {fails}/{total} recent failures in '{category}'"],
            )
        bump = int(rate * 40)
        if bump:
            score += bump
            reasons.append(f"+{bump}: {fails}/{total} recent failures")
        else:
            reasons.append(f"clean recent history ({total} outcomes, 0 failures)")

    # Eligibility guardrail: categories no human has signed off on never
    # proceed autonomously, however low their score.
    if category not in AUTONOMY_ELIGIBLE:
        return RiskAssessment(2, score, category, reasons + [f"'{category}' is not autonomy-eligible"])

    if score <= TIER0_MAX_SCORE and not unproven:
        # Tier 0 is earned, never default: a category with no track record
        # can be at most Tier 1, however low its base risk.
        return RiskAssessment(0, score, category, reasons)
    if score <= TIER1_MAX_SCORE:
        return RiskAssessment(1, score, category, reasons)
    return RiskAssessment(2, score, category, reasons)
