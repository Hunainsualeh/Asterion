"""Attachment extraction/context + deep-research DAG shape."""
from __future__ import annotations

from app.config import get_settings
from app.dag.engine import DagSpec
from app.dag.workflows import build_deep_research_nodes
from app.services import attachments


def test_kind_for():
    assert attachments.kind_for("report.pdf") == "pdf"
    assert attachments.kind_for("shot.PNG") == "image"
    assert attachments.kind_for("notes.md") == "text"
    assert attachments.kind_for("bin.exe") == "other"


def test_augment_query():
    assert attachments.augment_query("hi", "") == "hi"
    out = attachments.augment_query("summarize this", "DOC BODY")
    assert "DOC BODY" in out and "summarize this" in out


def test_deep_research_dag_is_valid_and_single_root():
    # A single planner root that expands into workers + a final report at runtime.
    spec = DagSpec(build_deep_research_nodes("some topic"))
    assert list(spec.nodes) == ["research_plan"]


async def test_stage_extract_consume_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", tmp_path)

    batch_id, metas = await attachments.stage_batch([
        ("notes.md", b"# Notes\nShip date is March 3."),
        ("data.csv", b"a,b\n1,2\n"),
    ])
    assert {m.name for m in metas} == {"notes.md", "data.csv"}
    assert all(m.kind == "text" for m in metas)

    ctx = attachments.context_for(batch_id)
    assert "### notes.md" in ctx and "March 3" in ctx

    docs = tmp_path / "docs"
    consumed = attachments.consume(batch_id, docs)
    assert "March 3" in consumed
    assert (docs / "attachments.md").exists()
    assert not (tmp_path / batch_id).exists()  # staging cleaned up
