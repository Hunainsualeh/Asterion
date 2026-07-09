"""Context condensation: summarize the middle of a transcript instead of
throwing it away.

The old strategy (`app.agents.base._trim_messages`) truncated the oldest tool
outputs to 300 characters and long messages to 1200, in place. It kept the
request under the token cap, but the information was simply *gone* — an agent
that had already read a file, run a test, and seen the failure would, twenty
turns later, be looking at `...[trimmed]` and re-running the same test.

This is the OpenHands `LLMSummarizingCondenser` approach: when the transcript
grows past `max_messages`, hand the middle span to a cheap, fast model and
replace it with a summary of what was learned and what remains. Reported there
as roughly halving cost with no measured loss in task performance. On Groq's
free tier the win is not really cost — it is that a long agent run stops 413ing.

Two invariants make this safe, and both are easy to get wrong:

1. **Tool-call pairing.** OpenAI-shaped transcripts require that every
   `{"role": "tool", "tool_call_id": X}` be preceded by an assistant message
   whose `tool_calls` contains X. Summarizing a span that begins or ends in the
   middle of such a pair produces a 400 from the provider, not a nice error. The
   span boundaries are therefore snapped outward to enclose whole groups.

2. **Never fail the run.** The summarizer is itself an LLM call, on the same
   rate-limited pool. If it errors, times out, or returns nothing, we fall back
   to the old truncating trim rather than propagating. A degraded transcript
   beats a dead pipeline.
"""
from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger("asterion.condenser")

# ~4 chars/token. Mirrors app/agents/base.py's budget: free-tier Groq caps a
# single request as low as 6-8K tokens on llama-3.1-8b and gpt-oss-20b.
CONTEXT_CHAR_BUDGET = 22_000
TRIM_KEEP_LAST = 6

_SUMMARY_MARKER = "[EARLIER CONVERSATION — CONDENSED]"

_SUMMARY_PROMPT = """You are compressing the middle of an AI agent's working transcript so the agent can keep going without re-reading it.

Write a dense, factual summary under 250 words. Preserve, in this order:
1. The user's goal, verbatim in intent.
2. What the agent has already established as TRUE — files read, their key contents, commands run and their exact outcomes, tests that passed or failed and how.
3. Decisions taken and the reason for each.
4. What is still unfinished.

Rules:
- Keep exact file paths, function names, error messages, and test names. They are what the agent needs to avoid repeating work.
- Do not speculate, advise, or add anything not present in the transcript.
- No preamble. Output only the summary."""


def total_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


# --------------------------------------------------------------------------- pairing
def _tool_call_ids(message: dict) -> set[str]:
    return {tc.get("id") for tc in (message.get("tool_calls") or []) if tc.get("id")}


def _group_starts(messages: list[dict]) -> list[int]:
    """Indices at which it is legal to cut the transcript.

    A cut is legal immediately before any message that is not a `tool` result,
    and not an assistant message whose tool results follow it. In practice: the
    start of each assistant-with-tool-calls block, and each standalone message.
    """
    starts: list[int] = []
    i = 0
    while i < len(messages):
        starts.append(i)
        if messages[i].get("tool_calls"):
            # Swallow the tool results answering this assistant turn.
            pending = _tool_call_ids(messages[i])
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                pending.discard(messages[i].get("tool_call_id"))
                i += 1
        else:
            i += 1
    return starts


def _snap(index: int, starts: list[int], *, forward: bool) -> int:
    """Move `index` to the nearest legal cut point, outward from the span so we
    never bisect an assistant/tool group."""
    if index in starts:
        return index
    if forward:
        later = [s for s in starts if s > index]
        return later[0] if later else index
    earlier = [s for s in starts if s < index]
    return earlier[-1] if earlier else index


# --------------------------------------------------------------------------- rendering
def _render(messages: list[dict]) -> str:
    out: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = str(m.get("content") or "").strip()
        if calls := m.get("tool_calls"):
            names = ", ".join((tc.get("function") or {}).get("name", "?") for tc in calls)
            out.append(f"[{role} called: {names}]" + (f" {content}" if content else ""))
        elif role == "tool":
            out.append(f"[tool result] {content[:1200]}")
        elif content:
            out.append(f"[{role}] {content[:1500]}")
    return "\n".join(out)


# --------------------------------------------------------------------------- legacy trim
def trim_messages(messages: list[dict]) -> None:
    """The original in-place truncating trim. Still the last line of defence:
    used when the summarizer is unavailable, and after condensing if the result
    is somehow still over budget."""
    if total_chars(messages) <= CONTEXT_CHAR_BUDGET:
        return
    for m in messages[1:-TRIM_KEEP_LAST]:
        if m.get("role") == "tool" and len(m.get("content") or "") > 300:
            m["content"] = str(m["content"])[:300] + "...[trimmed: full output was shown earlier in this session]"
            if total_chars(messages) <= CONTEXT_CHAR_BUDGET:
                return
    for m in messages[1:-TRIM_KEEP_LAST]:
        content = m.get("content")
        if isinstance(content, str) and len(content) > 1200:
            m["content"] = content[:1200] + "...[trimmed]"
            if total_chars(messages) <= CONTEXT_CHAR_BUDGET:
                return


# --------------------------------------------------------------------------- condenser
def should_condense(messages: list[dict]) -> bool:
    s = get_settings()
    if not s.condenser_enabled:
        return False
    if any(str(m.get("content") or "").startswith(_SUMMARY_MARKER) for m in messages):
        # Already condensed once. Re-condensing a transcript that is mostly a
        # summary loses more than it saves; let trim_messages hold the line.
        return len(messages) > s.condenser_max_messages * 2
    return len(messages) > s.condenser_max_messages or total_chars(messages) > CONTEXT_CHAR_BUDGET


async def condense(messages: list[dict], *, agent: str = "") -> list[dict]:
    """Return a shorter transcript with the same meaning.

    Never raises, never mutates the input list's identity contract beyond what
    `trim_messages` already did. Returns the original list when there is nothing
    safe to compress.
    """
    s = get_settings()
    if not should_condense(messages):
        return messages

    starts = _group_starts(messages)
    lo = _snap(min(s.condenser_keep_first, len(messages)), starts, forward=True)
    hi = _snap(max(len(messages) - s.condenser_keep_last, lo), starts, forward=False)

    span = messages[lo:hi]
    # Fewer than two groups is not worth an LLM call, and a one-message span
    # would summarize to something longer than itself.
    if hi <= lo or len(span) < 4:
        trim_messages(messages)
        return messages

    try:
        summary = await _summarize(span)
    except Exception as exc:  # noqa: BLE001 — condensing must never kill a run
        # Includes LLMError (the summarizer shares the rate-limited key pool it
        # is trying to relieve) and anything else the fast model does.
        log.warning("Condenser failed (%s); falling back to truncating trim", exc)
        trim_messages(messages)
        return messages

    if not summary:
        trim_messages(messages)
        return messages

    condensed = [
        *messages[:lo],
        {"role": "user", "content": f"{_SUMMARY_MARKER}\n{summary}"},
        *messages[hi:],
    ]
    log.info(
        "%scondensed %d messages (%d chars) -> summary (%d chars); transcript %d -> %d msgs",
        f"{agent}: " if agent else "",
        len(span), total_chars(span), len(summary), len(messages), len(condensed),
    )

    # The summary itself can still leave us over budget if the tail is huge.
    trim_messages(condensed)
    return condensed


async def _summarize(span: list[dict]) -> str:
    # Imported here: app.llm.client imports the provider clients, which import
    # app.config — importing it at module scope makes app.llm.condenser part of
    # that cycle for anything that imports condenser early.
    from app.llm.client import chat_completion

    response = await chat_completion(
        messages=[
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": _render(span)},
        ],
        # The cheap, high-quota model on purpose: summarizing runs *because* the
        # expensive model's budget is under pressure. Paying 70B rates to save
        # 70B tokens would defeat the exercise.
        model=get_settings().groq_fast_model,
        temperature=0.0,
        max_tokens=500,
        timeout_s=20.0,
    )
    return (response.choices[0].message.content or "").strip()
