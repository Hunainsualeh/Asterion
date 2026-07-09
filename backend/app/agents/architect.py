"""Agent 2 — Architecture Designer (Senior Architect).

Consults past ADRs from the Knowledge Store before designing: the closest
prior decisions are injected into the opening context so a new design never
silently contradicts an earlier one, and the `search_past_decisions` tool
covers follow-up lookups mid-design.
"""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.knowledge import store
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"ask_human", "submit_architecture"}


async def _past_decisions_block(scope_doc: str) -> str:
    hits = await store.search("adr", scope_doc[:2000], top_k=3)
    if not hits:
        return ""
    lines = ["\nPast architecture decisions — don't contradict these without stating why:"]
    for h in hits:
        lines.append(f"- {h['title']}: {h['body'][:300]}")
    return "\n".join(lines)


def _build_context(state: dict, past_decisions: str = "") -> str:
    parts = [f"Approved scope:\n{state.get('scope_doc', '')}"]
    if past_decisions:
        parts.append(past_decisions)

    qa = state.get("architecture_qa") or []
    if qa:
        parts.append("\nClarifications so far:")
        for round_ in qa:
            questions = "; ".join(round_.get("questions", []))
            parts.append(f"- Asked: {questions}\n  Answered: {round_.get('answer', '')}")

    feedback = state.get("architecture_feedback")
    if feedback:
        parts.append(f"\nThe human rejected the previous architecture draft with this feedback: {feedback}")
        parts.append("Revise the design to address it (ask more questions first if needed).")

    if len(qa) >= 3:
        parts.append(
            "\nYou have already asked several rounds of questions. Unless a decision is still "
            "genuinely blocking, call submit_architecture now using your best professional judgment."
        )

    return "\n".join(parts)


async def run(state: dict) -> tuple[str, dict]:
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="architect")
    past_decisions = await _past_decisions_block(state.get("scope_doc", ""))
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("architect"),
        user_messages=[{"role": "user", "content": _build_context(state, past_decisions)}],
        terminal_tools=TERMINAL_TOOLS,
    )
    return result.tool, result.args
