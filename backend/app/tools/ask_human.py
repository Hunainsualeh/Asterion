"""The ask-human tool.

Calling this tool doesn't pause anything by itself — the handler just echoes
the questions back. The agent node's tool loop watches for this specific call
and, when it sees one, stops the loop and hands control to a dedicated "wait"
graph node that does the actual `interrupt()`. Keeping the pause at the node
boundary (not inside the tool-calling loop) means a resume only replays the
trivial wait node, never re-runs the Groq calls that came before it.
"""
from __future__ import annotations

from app.tools.registry import ToolContext, register

ASK_HUMAN_TOOL = "ask_human"


@register(
    name=ASK_HUMAN_TOOL,
    description=(
        "Ask the human one or more clarifying questions and stop until they answer. "
        "Use this whenever a requirement is missing, ambiguous, or depends on a "
        "preference you cannot infer. Do not guess — ask."
    ),
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more specific, clearly-worded questions.",
            }
        },
        "required": ["questions"],
    },
    agents=["scope", "architect"],
)
async def ask_human(ctx: ToolContext, questions: list[str]) -> dict:
    return {"questions": questions}
