"""Human-facing translation layer.

Everything an agent node publishes via `publish_event` is written for two
different readers at once: the raw `kind`/`agent`/`message`/`data` fields are
a technical log (surfaced in the UI's optional "Activity" drawer for anyone
who wants it), while `describe_event()` below turns the same event into a
short, conversational line for the main chat thread — the thing a
non-technical user actually reads.

`describe_error()` does the same job for exceptions: it turns whatever the
Groq client or a tool raised into a plain-language explanation and a next
step, without leaking stack traces or provider error codes into the product.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

GATE_HEADLINES: dict[str, str] = {
    "APPROVE_SCOPE": "I've written up the scope for your project — can you take a look?",
    "APPROVE_ARCHITECTURE": "Here's the design I'd use to build this — sound good?",
    "APPROVE_TICKETS": "I've broken the work into tasks — approve them and I'll start building.",
    "MANUAL_TEST": "This part is ready — can you try it out and let me know if it works?",
    "SCOPE_CLARIFY": "Quick question before I keep going:",
    "ARCHITECTURE_CLARIFY": "One more thing I want to check before I design this:",
}

AGENT_STARTED_HEADLINES: dict[str, str] = {
    "scope": "Reading through your idea...",
    "architect": "Sketching out how this should be built...",
    "planner": "Breaking the work into small, buildable tasks...",
    "developer": "Writing the code...",
    "reviewer": "Double-checking the work...",
    "debugger": "Tracking down the issue...",
    "security": "Scanning the change for security issues...",
    "test": "Running the automated tests...",
}

DONE_HEADLINE = "All done — every task passed. Your project is complete."

# Task-lane executor agents, presented as the named specialists they act as.
# The node_started headline is what the live "who's working" indicator shows.
TASK_AGENT_ACTIVITY: dict[str, str] = {
    "code_plan": "Tech Lead is planning the build...",
    "designer": "UI/UX Designer is designing the interface...",
    "code": "Software Engineer is writing the code...",
    "code_review": "QA Engineer is reviewing and testing the code...",
    "research": "Researcher is gathering information...",
    "analyze": "Analyst is working through the details...",
    "summarize": "Putting the final answer together...",
    "planner": "Breaking the work into parallel subtasks...",
    "answer": "Thinking through your request...",
    "weather": "Checking the weather...",
}


def _ticket_phrase(data: dict[str, Any]) -> str:
    ticket = data.get("ticket") if isinstance(data, dict) else None
    if isinstance(ticket, dict) and ticket.get("title"):
        return f'"{ticket["title"]}"'
    if isinstance(ticket, dict) and ticket.get("id"):
        return ticket["id"]
    return "this part"


@dataclass
class Friendly:
    """A conversational rendering of a pipeline event."""

    headline: str
    detail: str | None = None
    tone: str = "info"  # info | progress | waiting | success | error
    chat: bool = True  # False = technical-only, don't show as its own chat bubble


def describe_event(kind: str, agent: str, message: str, data: dict[str, Any] | None = None) -> Friendly:
    data = data or {}

    if kind == "running":
        return Friendly("Getting to work...", tone="progress", chat=False)

    if kind == "agent_started":
        if agent == "developer":
            headline = f"Building {_ticket_phrase(data)}..."
        elif agent == "reviewer":
            headline = f"Reviewing {_ticket_phrase(data)}..."
        elif agent == "debugger":
            headline = f"Fixing an issue in {_ticket_phrase(data)}..."
        else:
            headline = AGENT_STARTED_HEADLINES.get(agent, "Working on it...")
        return Friendly(headline, tone="progress", chat=False)

    if kind == "agent_message":
        questions = data.get("questions")
        if isinstance(questions, list) and questions:
            # Clarifying questions get their own QuestionCard from the follow-up
            # `gate` event; the "asking clarifying questions" ping is redundant
            # for the chat thread but useful in the technical log.
            return Friendly("Thinking about what to ask...", tone="progress", chat=False)
        return Friendly(_agent_message_headline(agent, message, data), tone="info", chat=False)

    if kind == "gate":
        gate = data.get("gate", "")
        headline = GATE_HEADLINES.get(gate, "I need your input to continue.")
        detail = None
        if gate in ("SCOPE_CLARIFY", "ARCHITECTURE_CLARIFY"):
            qs = data.get("questions") or []
            detail = "\n".join(f"• {q}" for q in qs) if qs else None
        return Friendly(headline, detail, tone="waiting")

    if kind == "awaiting_input":
        # Superseded in the chat thread by the `gate` event with the same
        # payload; kept only for the technical log.
        return Friendly(message or "Waiting for input...", tone="waiting", chat=False)

    if kind == "digest":
        # Tier-1 auto-approval: visible in chat so the human's async digest
        # actually reaches them, but marked success — nothing is being asked.
        risk = data.get("risk") or {}
        return Friendly(
            f"Shipped automatically (low risk, tier {risk.get('tier', 1)}): {_ticket_phrase(data)}",
            tone="success",
        )

    if kind == "audit":
        # Tier-0: audit trail only — technical log, no chat bubble.
        return Friendly(message or "Auto-approved (tier 0)", tone="success", chat=False)

    if kind == "user_message":
        # The user's own message. The chat UI renders it as a user bubble
        # straight from the raw event, so no assistant-side bubble here.
        return Friendly(message or "Message received", tone="info", chat=False)

    if kind == "done":
        if data.get("lane") == "task":
            # Task-lane answers end with the result bubble itself — project
            # completion copy ("your project is complete") would be absurd
            # after a greeting or a quick question.
            return Friendly("Done.", tone="success", chat=False)
        return Friendly(DONE_HEADLINE, tone="success")

    if kind == "result":
        # The final deliverable itself — always chat-visible. The UI renders
        # the full markdown from data["result"]; detail carries a preview so
        # even a degraded client shows substance, never a bare headline.
        text = str(data.get("result") or "")
        headline = "Here's what I found:" if not data.get("partial") else "Here's what I completed:"
        return Friendly(headline, detail=text[:600] or None, tone="success")

    if kind == "ticket_done":
        summary = str(data.get("summary") or "").strip()
        return Friendly(
            f"Finished {_ticket_phrase(data)}.",
            detail=summary[:400] or None,
            tone="success",
        )

    if kind == "cancelled":
        return Friendly("Stopped — nothing else will run until you say so.", tone="info")

    if kind == "dag_started":
        n = len((data.get("dag") or {}).get("nodes") or [])
        return Friendly(f"Working on it — {n} step{'s' if n != 1 else ''} planned...", tone="progress", chat=False)

    if kind == "node_started":
        # Identity matters here: this headline drives the live "who is
        # working right now" indicator in the chat.
        activity = TASK_AGENT_ACTIVITY.get(agent)
        node = data.get("node") or {}
        return Friendly(activity or f"{node.get('name', 'step')} started...", tone="progress", chat=False)

    if kind in ("node_finished", "node_retry", "node_skipped"):
        node = data.get("node") or {}
        verb = {"node_finished": "finished", "node_retry": "retrying", "node_skipped": "skipped"}[kind]
        return Friendly(f"{node.get('name', 'step')}: {verb}", tone="progress", chat=False)

    if kind == "node_failed":
        node = data.get("node") or {}
        return Friendly(f"Step '{node.get('name', '?')}' hit a problem — continuing with the rest.",
                        tone="info", chat=False)

    if kind == "dag_finished":
        status = data.get("status", "")
        if status == "succeeded":
            return Friendly("All steps finished.", tone="success", chat=False)
        return Friendly(f"Run ended: {status}", tone="info", chat=False)

    if kind == "tool_call":
        return Friendly(message or "Using a tool...", tone="progress", chat=False)

    if kind == "error":
        fe = data.get("friendly_error") or {}
        headline = fe.get("title") or "Something went wrong"
        detail = fe.get("explanation") or None
        return Friendly(headline, detail, tone="error")

    if kind == "project_updated":
        return Friendly("Updated the project summary.", tone="info", chat=False)

    if kind == "ui_action":
        # Handled by the client's ActionExecutor, not rendered as a chat bubble.
        return Friendly(message or "Running an app action...", tone="info", chat=False)

    return Friendly(message or kind.replace("_", " "), tone="info", chat=False)


def _agent_message_headline(agent: str, message: str, data: dict[str, Any]) -> str:
    if agent == "scope":
        return "Scope is ready for your review."
    if agent == "architect":
        return "Architecture is ready for your review."
    if agent == "planner":
        count = message.split(" ")[1] if message.startswith("Created ") else None
        return f"Split the work into {count} tasks." if count else "Tasks are ready for your review."
    if agent == "developer":
        return f"Finished the first pass on {_ticket_phrase(data)} — sending it for review."
    if agent == "reviewer":
        if "passed" in message.lower():
            return f"Review passed for {_ticket_phrase(data)}."
        return f"Found a few things to fix in {_ticket_phrase(data)} — sending it back."
    if agent == "debugger":
        return f"Applied a fix for {_ticket_phrase(data)} and sent it back for review."
    if agent == "docs":
        return f"{_ticket_phrase(data)} passed — wrapping up the notes."
    return message


@dataclass
class FriendlyError:
    title: str
    explanation: str
    suggestion: str
    retryable: bool
    reference: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def describe_error(exc: Exception) -> FriendlyError:
    """Translate an exception raised mid-pipeline into plain language.

    The full exception is always logged server-side (see runner.py) before
    this is called — nothing here needs to preserve stack traces or provider
    error codes for the user, only a short reference id to correlate with
    those logs if they report the problem.
    """
    from app.agents.base import ToolLoopExhausted
    from app.llm.errors import (
        LLMConnectionError,
        LLMError,
        LLMTimeout,
        OverCapacity,
        ProviderUnavailable,
        RateLimited,
        RequestTooLarge,
    )

    # ProviderUnavailable is checked before RateLimited: only the user can fix
    # a dead key or an unpaid balance, so "wait and retry" would be a lie.
    if isinstance(exc, ProviderUnavailable):
        provider = getattr(exc, "provider", "") or "the AI provider"
        return FriendlyError(
            title=f"I can't use {provider} right now",
            explanation=str(exc),
            suggestion=(
                "Fix the provider's API key or billing, or pick a different model "
                "in Settings › Models. Everything else keeps working."
            ),
            retryable=False,
        )

    if isinstance(exc, RateLimited):
        return FriendlyError(
            title="I'm briefly out of capacity",
            explanation="The AI service I rely on is rate-limiting requests right now.",
            suggestion="Wait a minute or two, then press Try again.",
            retryable=True,
        )

    if isinstance(exc, (LLMConnectionError, LLMTimeout)):
        return FriendlyError(
            title="I couldn't reach the AI service",
            explanation="There was a network problem talking to the AI provider.",
            suggestion="Check your connection and press Try again.",
            retryable=True,
        )

    if isinstance(exc, OverCapacity):
        return FriendlyError(
            title="The AI service is having trouble",
            explanation="The provider I rely on is temporarily unavailable.",
            suggestion="This is usually short-lived — press Try again in a moment.",
            retryable=True,
        )

    if isinstance(exc, ToolLoopExhausted):
        return FriendlyError(
            title="I got stuck making a decision",
            explanation="I went back and forth without reaching a clear next step.",
            suggestion="Press Try again — rephrasing your last answer with more detail also helps.",
            retryable=True,
        )

    if isinstance(exc, RequestTooLarge):
        return FriendlyError(
            title="That was too much to think about at once",
            explanation="The conversation grew past what the current model can read in one request.",
            suggestion="Start a new chat, or pick a longer-context model in Settings › Models.",
            retryable=False,
        )

    # Anything else from the LLM layer: a 400 we built wrong, an unmapped
    # status. Checked last — every branch above is an LLMError subclass.
    if isinstance(exc, LLMError):
        return FriendlyError(
            title="The AI service rejected a request",
            explanation="Something about the last request wasn't accepted by the AI provider.",
            suggestion="Press Try again. If it keeps happening, try rephrasing your project idea.",
            retryable=True,
        )

    if isinstance(exc, (ConnectionError, OSError)):
        return FriendlyError(
            title="A connection problem got in the way",
            explanation="I couldn't reach a service I depend on (storage or network).",
            suggestion="Press Try again in a moment.",
            retryable=True,
        )

    return FriendlyError(
        title="Something went wrong on my end",
        explanation="I hit an unexpected problem and had to stop.",
        suggestion="Press Try again. If it keeps happening, start a new project with a bit more detail.",
        retryable=True,
    )
