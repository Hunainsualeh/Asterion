"""Agent 1 — Scope Discovery (Product Owner)."""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"ask_human", "submit_scope"}


def _build_context(state: dict) -> str:
    parts = [f"Project idea:\n{state.get('raw_idea', '')}"]

    qa = state.get("scope_qa") or []
    if qa:
        parts.append("\nClarifications so far:")
        for round_ in qa:
            questions = "; ".join(round_.get("questions", []))
            parts.append(f"- Asked: {questions}\n  Answered: {round_.get('answer', '')}")

    feedback = state.get("scope_feedback")
    if feedback:
        parts.append(f"\nThe human rejected the previous scope draft with this feedback: {feedback}")
        parts.append("Revise the scope to address it (ask more questions first if the feedback raises new ambiguity).")

    if len(qa) >= 4:
        parts.append(
            "\nYou have already asked several rounds of questions. Unless something is still "
            "critically unclear, call submit_scope now using reasonable assumptions for anything minor."
        )

    return "\n".join(parts)


async def run(state: dict) -> tuple[str, dict]:
    """Runs the scope tool loop. Returns (terminal_tool, args)."""
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="scope")
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("scope"),
        user_messages=[{"role": "user", "content": _build_context(state)}],
        terminal_tools=TERMINAL_TOOLS,
    )
    return result.tool, result.args
