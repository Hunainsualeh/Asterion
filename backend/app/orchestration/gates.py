"""Human approval gates.

Each gate uses LangGraph's `interrupt()` to freeze the graph and surface a
payload to the UI. The graph resumes when the API calls
`ainvoke(Command(resume=<decision>), config)`.

Decision shapes expected on resume:
  approval gates: {"action": "approve"|"reject", "feedback": "<optional text>"}
  manual test   : {"result": "pass"|"fail",     "feedback": "<optional text>"}
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

# Gate identifiers (also the labels shown in the UI).
GATE_SCOPE = "APPROVE_SCOPE"
GATE_ARCHITECTURE = "APPROVE_ARCHITECTURE"
GATE_TICKETS = "APPROVE_TICKETS"
GATE_MANUAL_TEST = "MANUAL_TEST"
GATE_SCOPE_CLARIFY = "SCOPE_CLARIFY"
GATE_ARCHITECTURE_CLARIFY = "ARCHITECTURE_CLARIFY"

ALL_GATES = (
    GATE_SCOPE,
    GATE_ARCHITECTURE,
    GATE_TICKETS,
    GATE_MANUAL_TEST,
    GATE_SCOPE_CLARIFY,
    GATE_ARCHITECTURE_CLARIFY,
)


def request_approval(gate: str, summary: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Pause for a human approve/reject decision. Returns the resume value."""
    decision = interrupt(
        {
            "kind": "approval",
            "gate": gate,
            "summary": summary,
            "payload": payload,
        }
    )
    return decision if isinstance(decision, dict) else {"action": str(decision)}


def request_clarification(gate: str, questions: list[str]) -> dict[str, Any]:
    """Pause for answers to an agent's clarifying questions (mid-stage, not a final approval)."""
    decision = interrupt(
        {
            "kind": "clarify",
            "gate": gate,
            "summary": "Answer the agent's clarifying questions.",
            "payload": {"questions": questions},
        }
    )
    return decision if isinstance(decision, dict) else {"feedback": str(decision)}


def request_manual_test(payload: dict[str, Any]) -> dict[str, Any]:
    """Pause for the human PASS/FAIL manual-test result. Returns the resume value."""
    decision = interrupt(
        {
            "kind": "manual_test",
            "gate": GATE_MANUAL_TEST,
            "summary": "Manually test this ticket, then mark PASS or FAIL.",
            "payload": payload,
        }
    )
    return decision if isinstance(decision, dict) else {"result": str(decision)}


def is_approved(decision: dict[str, Any]) -> bool:
    return str(decision.get("action", "")).lower() == "approve"


def is_pass(decision: dict[str, Any]) -> bool:
    return str(decision.get("result", "")).lower() == "pass"
