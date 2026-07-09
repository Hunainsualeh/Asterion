"""Agent 6 — Debugger, invoked when a test fails.

Checks the cross-project fix history before deriving anything from scratch:
the closest past incidents (by symptom similarity) are injected into the
opening context, and the `search_fix_history` tool covers follow-up lookups
mid-investigation.
"""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.knowledge import store
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"submit_fix"}
MAX_ITERATIONS = 20


async def _fix_history_block(symptom: str) -> str:
    hits = await store.search("incident", symptom, top_k=3)
    if not hits:
        return ""
    lines = ["\nSimilar incidents from the fix history — check these before re-deriving a fix:"]
    for h in hits:
        lines.append(f"- Symptom: {h['title']}\n  Fix that worked: {h['meta'].get('fix', '')[:400]}")
    return "\n".join(lines)


def _build_context(state: dict, ticket: dict, fix_history: str) -> str:
    return "\n".join(
        [
            f"Approved architecture:\n{state.get('architecture_doc', '')}",
            f"\nTicket ({ticket.get('id')}): {ticket.get('title')}",
            f"Description: {ticket.get('description', '')}",
            f"Branch: {state.get('branch', '')}",
            f"\nThe test FAILED with this report:\n{state.get('test_feedback', '')}",
            fix_history,
        ]
    )


async def run(state: dict, ticket: dict) -> str:
    """Runs the debugger tool loop. Returns the fix summary string."""
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="debugger")
    fix_history = await _fix_history_block(state.get("test_feedback", ""))
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("debugger"),
        user_messages=[{"role": "user", "content": _build_context(state, ticket, fix_history)}],
        terminal_tools=TERMINAL_TOOLS,
        max_iterations=MAX_ITERATIONS,
    )
    return result.args.get("summary", "")
