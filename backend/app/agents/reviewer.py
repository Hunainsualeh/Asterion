"""Agent 5 — Code Reviewer (Lead Engineer)."""
from __future__ import annotations

from app.agents.base import run_tool_loop
from app.llm import prompts
from app.tools.registry import ToolContext

TERMINAL_TOOLS = {"submit_review"}
MAX_ITERATIONS = 12


def _build_context(state: dict, ticket: dict) -> str:
    parts = [
        f"Approved architecture:\n{state.get('architecture_doc', '')}",
        f"\nTicket being reviewed ({ticket.get('id')}): {ticket.get('title')}",
        f"Description: {ticket.get('description', '')}",
        f"Acceptance criteria: {ticket.get('acceptance_criteria', [])}",
        f"Branch: {state.get('branch', '')}",
    ]
    notes = state.get("debug_notes") or state.get("dev_notes")
    if notes:
        who = "Debugger" if state.get("debug_notes") else "Developer"
        parts.append(f"\n{who}'s summary of the change: {notes}")
    findings = state.get("security_findings") or []
    if findings:
        lines = [f"- [{f.get('severity')}] {f.get('kind')} at {f.get('file')}:{f.get('line')}" for f in findings[:10]]
        parts.append(
            "\nSecurity scan findings — verify each is addressed or explicitly acceptable before approving:\n"
            + "\n".join(lines)
        )
    if state.get("test_result") == "fail":
        parts.append(f"\nThis is a re-review after a test failure: {state.get('test_feedback', '')}")
    return "\n".join(parts)


async def run(state: dict, ticket: dict) -> tuple[bool, str]:
    """Runs the reviewer tool loop. Returns (approved, notes)."""
    pid = state["project_id"]
    ctx = ToolContext(project_id=pid, agent="reviewer")
    result = await run_tool_loop(
        ctx,
        system_prompt=prompts.load("reviewer"),
        user_messages=[{"role": "user", "content": _build_context(state, ticket)}],
        terminal_tools=TERMINAL_TOOLS,
        max_iterations=MAX_ITERATIONS,
    )
    return bool(result.args.get("approved", False)), result.args.get("notes", "")
