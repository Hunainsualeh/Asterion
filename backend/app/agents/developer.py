"""Agent 4 — Developer (Software Engineer), one ticket at a time.

Prompt-level learning: recent first-try-review-pass exemplars from the same
ticket category are injected as few-shot context. This is how the Build pool
"learns" on top of fixed Groq models — no weight updates, just retrieval.
"""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.knowledge import store
from app.knowledge.classify import categorize
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"submit_dev_done"}
MAX_ITERATIONS = 20


async def _exemplars_block(ticket: dict) -> str:
    exemplars = await store.recent_exemplars(categorize(ticket), limit=2)
    if not exemplars:
        return ""
    lines = ["\nPast work in this category that passed review on the first try — match this bar:"]
    for e in exemplars:
        lines.append(f"- {e['title']}: {e['body'][:300]}")
    return "\n".join(lines)


def _build_context(state: dict, ticket: dict, exemplars: str = "") -> str:
    parts = [
        f"Approved architecture:\n{state.get('architecture_doc', '')}",
        f"\nYour ticket ({ticket.get('id')}): {ticket.get('title')}",
        f"Description: {ticket.get('description', '')}",
        f"Acceptance criteria: {ticket.get('acceptance_criteria', [])}",
    ]
    if exemplars:
        parts.append(exemplars)
    review_notes = state.get("review_notes")
    if state.get("review_result") == "needs_fix" and review_notes:
        parts.append(f"\nThe Reviewer sent this back with feedback you must address:\n{review_notes}")
    return "\n".join(parts)


async def run(state: dict, ticket: dict) -> str:
    """Runs the developer tool loop. Returns the summary string."""
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="developer")
    exemplars = await _exemplars_block(ticket)
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("developer"),
        user_messages=[{"role": "user", "content": _build_context(state, ticket, exemplars)}],
        terminal_tools=TERMINAL_TOOLS,
        max_iterations=MAX_ITERATIONS,
    )
    return result.args.get("summary", "")
