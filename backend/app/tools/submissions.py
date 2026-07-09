"""Structured 'final answer' tools.

Each stage ends by calling one of these instead of returning plain text, so
the agent's output is parsed JSON, not scraped free-form prose. The base
agent loop (app/agents/base.py) intercepts these calls by name before
generic dispatch — the handlers below exist only so the tool has a Groq
schema and a `dispatch()` entry point if ever called directly (e.g. tests).
"""
from __future__ import annotations

from typing import Any

from app.tools.registry import ToolContext, register

_TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Short id, e.g. T-001."},
        "title": {"type": "string"},
        "description": {"type": "string", "description": "What to build."},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "test_checklist": {"type": "array", "items": {"type": "string"}, "description": "Manual test steps."},
        "dependencies": {"type": "array", "items": {"type": "string"}, "description": "ids of prerequisite tickets."},
        "effort": {"type": "string", "description": "S/M/L or an hour estimate."},
    },
    "required": ["id", "title", "description", "acceptance_criteria", "test_checklist", "dependencies", "effort"],
}


@register(
    name="submit_scope",
    description="Submit the finished scope document. Only call this once all clarifications are resolved.",
    parameters={
        "type": "object",
        "properties": {"doc": {"type": "string", "description": "Full scope document as markdown."}},
        "required": ["doc"],
    },
    agents=["scope"],
)
async def submit_scope(ctx: ToolContext, doc: str) -> dict[str, Any]:
    return {"doc": doc}


@register(
    name="submit_architecture",
    description="Submit the finished architecture document. Only call this once the design is complete.",
    parameters={
        "type": "object",
        "properties": {"doc": {"type": "string", "description": "Full architecture document as markdown."}},
        "required": ["doc"],
    },
    agents=["architect"],
)
async def submit_architecture(ctx: ToolContext, doc: str) -> dict[str, Any]:
    return {"doc": doc}


@register(
    name="submit_tickets",
    description="Submit the finished, dependency-ordered ticket list.",
    parameters={
        "type": "object",
        "properties": {"tickets": {"type": "array", "items": _TICKET_SCHEMA}},
        "required": ["tickets"],
    },
    agents=["planner"],
)
async def submit_tickets(ctx: ToolContext, tickets: list[dict]) -> dict[str, Any]:
    return {"tickets": tickets}


@register(
    name="submit_dev_done",
    description=(
        "Declare this ticket's implementation finished and ready for review. Only call this "
        "after the code is written, checks have been run, and everything is committed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What you implemented and how you verified it builds/runs."}
        },
        "required": ["summary"],
    },
    agents=["developer"],
)
async def submit_dev_done(ctx: ToolContext, summary: str) -> dict[str, Any]:
    return {"summary": summary}


@register(
    name="submit_review",
    description="Submit your review verdict for the current ticket's diff.",
    parameters={
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "description": "True if the code is acceptable to ship."},
            "notes": {
                "type": "string",
                "description": "If not approved: exactly what is wrong and what to fix. If approved: a brief justification.",
            },
        },
        "required": ["approved", "notes"],
    },
    agents=["reviewer"],
)
async def submit_review(ctx: ToolContext, approved: bool, notes: str) -> dict[str, Any]:
    return {"approved": approved, "notes": notes}


@register(
    name="submit_fix",
    description="Declare the bug fix finished, committed, and ready to go back through review.",
    parameters={
        "type": "object",
        "properties": {"summary": {"type": "string", "description": "What was wrong and what you changed to fix it."}},
        "required": ["summary"],
    },
    agents=["debugger"],
)
async def submit_fix(ctx: ToolContext, summary: str) -> dict[str, Any]:
    return {"summary": summary}
