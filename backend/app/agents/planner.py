"""Agent 3 — Project Planner (Technical PM)."""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"submit_tickets"}


def _build_context(state: dict) -> str:
    parts = [f"Approved architecture:\n{state.get('architecture_doc', '')}"]
    feedback = state.get("tickets_feedback")
    if feedback:
        parts.append(f"\nThe human rejected the previous ticket list with this feedback: {feedback}")
        parts.append("Revise the tickets to address it.")
    return "\n".join(parts)


async def run(state: dict) -> list[dict]:
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="planner")
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("planner"),
        user_messages=[{"role": "user", "content": _build_context(state)}],
        terminal_tools=TERMINAL_TOOLS,
    )
    return result.args.get("tickets", [])
