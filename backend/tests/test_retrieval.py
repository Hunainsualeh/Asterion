"""Retrieval injection: agents see seeded Knowledge Store records in context."""
from __future__ import annotations

import asyncio

import pytest

from app.agents.architect import _past_decisions_block
from app.agents.debugger import _fix_history_block
from app.agents.developer import _exemplars_block
from app.knowledge import store
from app.tools.knowledge_tools import search_fix_history, search_past_decisions
from app.tools.registry import ToolContext, tool_names_for


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "knowledge-test.db")


def run(coro):
    return asyncio.run(coro)


def test_debugger_sees_similar_incident():
    run(store.record_incident(
        "proj-old",
        symptom="ValueError: could not convert string to float in parse_args",
        fix="validate argv before float() and print a usage message on bad input",
    ))
    block = run(_fix_history_block("ValueError could not convert string to float when parsing arguments"))
    assert "parse_args" in block and "usage message" in block


def test_architect_sees_past_adr():
    run(store.record_adr("proj-old", "CLI tool design", "argparse, single file, stdlib only"))
    # Callers pass the (token-rich) scope doc, not a short abstract query —
    # that's the input shape the lexical embedder is good at.
    block = run(_past_decisions_block(
        "Scope: a command line converter tool in Python. Single file script, stdlib only, argparse for input."
    ))
    assert "CLI tool design" in block


def test_developer_sees_exemplars_for_category():
    run(store.record_exemplar("proj-old", "api", "Add /health endpoint", "Minimal FastAPI route with test."))
    block = run(_exemplars_block({"id": "T-9", "title": "Add /status endpoint", "description": "new api route"}))
    assert "/health" in block


def test_blocks_empty_when_store_empty():
    assert run(_fix_history_block("anything")) == ""
    assert run(_past_decisions_block("anything")) == ""
    assert run(_exemplars_block({"title": "x", "description": "y"})) == ""


def test_tools_registered_to_right_agents():
    assert "search_fix_history" in tool_names_for("debugger")
    assert "search_past_decisions" in tool_names_for("architect")
    assert "search_fix_history" not in tool_names_for("scope")


def test_tool_handlers_return_matches():
    run(store.record_incident("p", symptom="redis timeout on xread", fix="raise block_ms"))
    ctx = ToolContext(project_id="p", agent="debugger")
    out = run(search_fix_history(ctx, symptom="redis xread timeout"))
    assert out["matches"] and out["matches"][0]["fix"] == "raise block_ms"

    run(store.record_adr("p", "Queue choice", "Redis streams over pub/sub for replay"))
    ctx2 = ToolContext(project_id="p", agent="architect")
    out2 = run(search_past_decisions(ctx2, topic="redis streams or pub/sub for the event queue replay"))
    assert out2["decisions"] and out2["decisions"][0]["title"] == "Queue choice"
