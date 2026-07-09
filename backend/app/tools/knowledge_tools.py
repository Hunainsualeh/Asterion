"""Cross-project Knowledge Store retrieval tools.

Distinct from memory_tools (per-project scratch notes): these search the
permanent organizational memory — fix history for the Debugger, past
architecture decisions for the Architect. The same lookups are also injected
automatically into those agents' opening context (see app/agents), so the
tools exist for follow-up queries mid-loop, not as the only path to memory.
"""
from __future__ import annotations

from app.knowledge import store
from app.tools.registry import ToolContext, register


@register(
    name="search_fix_history",
    description=(
        "Search every past diagnosed incident across all projects by symptom/stack-trace similarity. "
        "Returns the closest matches with the fix that resolved each. Check this before deriving a fix "
        "from scratch — if a close match exists, propose the same class of fix."
    ),
    parameters={
        "type": "object",
        "properties": {
            "symptom": {"type": "string", "description": "Error message, stack trace, or failure description."},
            "top_k": {"type": "integer", "description": "Max matches to return.", "default": 3},
        },
        "required": ["symptom"],
    },
    agents=["debugger", "reviewer"],
)
async def search_fix_history(ctx: ToolContext, symptom: str, top_k: int = 3) -> dict:
    hits = await store.search("incident", symptom, top_k=top_k)
    return {
        "matches": [
            {"symptom": h["title"], "fix": h["meta"].get("fix", ""), "similarity": h["score"]}
            for h in hits
        ]
    }


@register(
    name="search_past_decisions",
    description=(
        "Search past architecture decisions (ADRs) across all projects. Check this before committing "
        "to a design so you don't contradict an earlier decision without saying why."
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "The design question or area to search for."},
            "top_k": {"type": "integer", "description": "Max matches to return.", "default": 3},
        },
        "required": ["topic"],
    },
    agents=["architect", "planner"],
)
async def search_past_decisions(ctx: ToolContext, topic: str, top_k: int = 3) -> dict:
    hits = await store.search("adr", topic, top_k=top_k)
    return {
        "decisions": [
            {"title": h["title"], "decision": h["body"][:1500], "similarity": h["score"]}
            for h in hits
        ]
    }
