"""Knowledge Store: write → search → ledger round-trips against a temp DB."""
from __future__ import annotations

import asyncio

import pytest

from app.knowledge import store
from app.knowledge.classify import categorize


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "knowledge-test.db")


def run(coro):
    return asyncio.run(coro)


def test_incident_roundtrip_by_symptom_similarity():
    run(store.record_incident(
        "proj-a",
        symptom="TypeError: cannot unpack non-iterable NoneType object in convert_temperature",
        fix="convert_temperature returned None on invalid input; return a (value, unit) tuple instead",
    ))
    run(store.record_incident(
        "proj-b",
        symptom="Redis connection refused on startup",
        fix="fall back to fakeredis when native Redis is unreachable",
    ))

    hits = run(store.search("incident", "TypeError NoneType unpack convert_temperature crash"))
    assert hits, "expected at least one incident hit"
    assert "convert_temperature" in hits[0]["title"]
    assert "tuple" in hits[0]["meta"]["fix"]


def test_adr_roundtrip():
    run(store.record_adr("proj-a", "CLI converter architecture", "Single-file argparse CLI, no deps, stdlib only."))
    hits = run(store.search("adr", "how should a small CLI tool be architected"))
    assert hits and hits[0]["title"] == "CLI converter architecture"


def test_outcome_ledger_failure_rate():
    ticket = {"id": "T-1", "title": "Add login endpoint"}
    cat = categorize(ticket)
    assert cat == "security"
    for result in ["pass", "fail", "fail"]:
        run(store.record_outcome("proj-a", ticket, cat, "developer", "review", result))
    fails, total = run(store.failure_rate("security"))
    assert (fails, total) == (2, 3)


def test_agent_track_record():
    ticket = {"id": "T-2", "title": "Update README"}
    run(store.record_outcome("proj-a", ticket, "docs", "developer", "review", "pass", revision_rounds=1))
    record = run(store.agent_track_record("developer"))
    assert record["docs"]["pass"] == 1


def test_exemplar_retrieval_by_category():
    run(store.record_exemplar("proj-a", "api", "Add /health endpoint", "Clean FastAPI route, passed review first try."))
    exemplars = run(store.recent_exemplars("api"))
    assert exemplars and exemplars[0]["title"] == "Add /health endpoint"
    assert run(store.recent_exemplars("payments")) == []


def test_search_empty_store_and_empty_query():
    assert run(store.search("incident", "anything")) == []
    assert run(store.search("adr", "   ")) == []
