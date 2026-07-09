"""Write-back hooks — pipeline step 13 (Docs + Learning), invoked inline.

Every decision and outcome flows into the Knowledge Store the moment it
happens, not in a batch later: approved architectures become ADRs, review and
test verdicts land in the outcome ledger, debugger resolutions become
searchable incidents, and first-try review passes become exemplars for future
few-shot context. The risk-tier system and the Debugger/Architect retrieval
are only as good as this ledger, so these hooks are wired into the graph
nodes themselves.

Learning failures must never fail the work itself — every hook swallows and
logs its own exceptions.
"""
from __future__ import annotations

import logging
from typing import Any

from app.knowledge import store
from app.knowledge.classify import categorize

log = logging.getLogger("asterion.knowledge.hooks")


async def architecture_approved(project_id: str, title: str, doc: str, qa: list[dict] | None) -> None:
    try:
        await store.record_adr(project_id, title or "Architecture decision", doc, {"qa": qa or []})
    except Exception:
        log.exception("ADR write-back failed for %s", project_id)


async def review_completed(
    project_id: str, ticket: dict[str, Any], approved: bool, rounds: int, notes: str, fixed_by_debugger: bool
) -> None:
    try:
        category = categorize(ticket)
        agent = "debugger" if fixed_by_debugger else "developer"
        await store.record_outcome(
            project_id, ticket, category, agent, "review",
            "pass" if approved else "fail", revision_rounds=rounds, detail=notes,
        )
        if approved and rounds == 1 and not fixed_by_debugger:
            # First-try pass: keep it as few-shot material for this category.
            await store.record_exemplar(
                project_id, category,
                title=str(ticket.get("title", "")), body=notes or str(ticket.get("description", "")),
                meta={"ticket_id": ticket.get("id")},
            )
    except Exception:
        log.exception("review write-back failed for %s", project_id)


async def test_completed(
    project_id: str, ticket: dict[str, Any], stage: str, passed: bool, detail: str, rounds: int = 0
) -> None:
    """stage: 'auto_test' | 'manual_test' | 'security'."""
    try:
        await store.record_outcome(
            project_id, ticket, categorize(ticket), "developer", stage,
            "pass" if passed else "fail", revision_rounds=rounds, detail=detail,
        )
    except Exception:
        log.exception("%s write-back failed for %s", stage, project_id)


async def incident_resolved(project_id: str, ticket: dict[str, Any], symptom: str, fix: str) -> None:
    try:
        await store.record_incident(
            project_id, symptom=symptom, fix=fix,
            meta={"ticket_id": ticket.get("id"), "ticket_title": ticket.get("title"), "category": categorize(ticket)},
        )
    except Exception:
        log.exception("incident write-back failed for %s", project_id)
