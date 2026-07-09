"""Tool-loop context budget: transcripts must stay under the tightest TPM cap."""
from __future__ import annotations

from app.agents.base import CONTEXT_CHAR_BUDGET, TRIM_KEEP_LAST, _trim_messages


def _transcript(n_tools: int, tool_len: int) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "You are the developer."}]
    for i in range(n_tools):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_len})
    msgs.append({"role": "user", "content": "continue"})
    return msgs


def total_chars(msgs):
    return sum(len(str(m.get("content") or "")) for m in msgs)


def test_oversized_transcript_gets_trimmed_under_budget():
    msgs = _transcript(n_tools=12, tool_len=5000)  # ~60K chars, way over budget
    assert total_chars(msgs) > CONTEXT_CHAR_BUDGET
    _trim_messages(msgs)
    assert total_chars(msgs) <= CONTEXT_CHAR_BUDGET


def test_small_transcript_untouched():
    msgs = _transcript(n_tools=2, tool_len=100)
    before = [dict(m) for m in msgs]
    _trim_messages(msgs)
    assert msgs == before


def test_recent_messages_and_structure_survive():
    msgs = _transcript(n_tools=12, tool_len=5000)
    tail_before = [str(m.get("content")) for m in msgs[-TRIM_KEEP_LAST:]]
    n_before = len(msgs)
    _trim_messages(msgs)
    assert len(msgs) == n_before                       # nothing deleted, only compacted
    assert [str(m.get("content")) for m in msgs[-TRIM_KEEP_LAST:]] == tail_before
    assert msgs[0]["content"] == "You are the developer."
    # every tool message still resolves its tool_call_id
    assert all("tool_call_id" in m for m in msgs if m.get("role") == "tool")
