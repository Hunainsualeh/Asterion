"""Ticket categorization — shared by the Knowledge Store and risk scoring.

A ticket's category drives two things: which past outcomes count toward its
risk score, and which exemplars are retrieved for few-shot context. Keyword
matching is deliberate — it's deterministic, auditable, and a wrong category
degrades gracefully (the risk tiers treat unknown as high-risk, never low).
First match wins, so order runs highest-blast-radius first.
"""
from __future__ import annotations

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("security", ("security", "auth", "login", "password", "token", "secret", "permission", "encrypt", "credential")),
    ("payments", ("payment", "billing", "invoice", "checkout", "stripe", "refund")),
    ("schema", ("schema", "migration", "database", "db table", "column", "index", "sql")),
    ("api", ("endpoint", "api", "route", "webhook", "rest", "graphql")),
    ("infra", ("deploy", "docker", "server", "infra", "pipeline", "ci", "queue", "cache", "redis")),
    ("ui", ("ui", "frontend", "page", "component", "css", "layout", "button", "form", "display")),
    ("tests", ("test", "coverage", "pytest", "unit test")),
    ("docs", ("readme", "documentation", "docs", "comment")),
    ("config", ("config", "setting", "env", "flag")),
]

DEFAULT_CATEGORY = "logic"


def categorize(ticket: dict) -> str:
    text = f"{ticket.get('title', '')} {ticket.get('description', '')}".lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return DEFAULT_CATEGORY
