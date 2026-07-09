"""The Knowledge Store — Asterion's long-term memory (doc Sections 08/09).

Holds everything the system learns across projects: architecture decisions
(ADRs), diagnosed incidents and their fixes, first-try-pass exemplars, and a
per-ticket outcome ledger that the risk-tier system scores against.

Backend is SQLite (stdlib, zero infra — this box runs no Docker/Postgres);
the schema is written to port 1:1 onto PostgreSQL + pgvector when the stack
moves there: `documents`+`embeddings` becomes one table with a `vector`
column, every query below has a direct pgvector equivalent.

"Self-learning" here is retrieval + feedback adaptation, NOT weight updates —
Groq-hosted models cannot be fine-tuned. The loop is: write outcomes back
after every ticket/incident/review (hooks.py), retrieve before acting
(Debugger searches incidents, Architect searches ADRs), and score autonomy
from the outcome ledger (risk.py).

All writes/reads run in a thread via asyncio.to_thread with a fresh WAL-mode
connection per call — plenty at this scale, no connection pool to babysit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.config import BACKEND_DIR
from app.knowledge.embedder import get_embedder

log = logging.getLogger("asterion.knowledge")

DB_PATH = BACKEND_DIR / "knowledge.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- 'adr' | 'incident' | 'exemplar'
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind);

CREATE TABLE IF NOT EXISTS embeddings (
    doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    ticket_title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    agent TEXT NOT NULL,               -- who produced the work being judged
    stage TEXT NOT NULL,               -- 'review' | 'security' | 'auto_test' | 'manual_test'
    result TEXT NOT NULL,              -- 'pass' | 'fail'
    revision_rounds INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_category ON ticket_outcomes(category);
CREATE INDEX IF NOT EXISTS idx_outcomes_agent ON ticket_outcomes(agent);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _insert_document(kind: str, project_id: str, title: str, body: str, meta: dict[str, Any]) -> int:
    embedder = get_embedder()
    vec = embedder.embed([f"{title}\n{body}"])[0]
    with _connect() as conn:
        _init(conn)
        cur = conn.execute(
            "INSERT INTO documents (kind, project_id, title, body, meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, project_id, title, body, json.dumps(meta, default=str), time.time()),
        )
        doc_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO embeddings (doc_id, dim, vector) VALUES (?, ?, ?)",
            (doc_id, len(vec), vec.astype(np.float32).tobytes()),
        )
    return doc_id


def _search(kind: str, query: str, top_k: int, min_score: float) -> list[dict[str, Any]]:
    embedder = get_embedder()
    qvec = embedder.embed([query])[0]
    with _connect() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT d.id, d.project_id, d.title, d.body, d.meta, d.created_at, e.vector, e.dim "
            "FROM documents d JOIN embeddings e ON e.doc_id = d.id WHERE d.kind = ?",
            (kind,),
        ).fetchall()

    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        if row["dim"] != len(qvec):
            continue  # embedder changed since this doc was written; skip rather than mis-rank
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        scored.append((float(np.dot(qvec, vec)), row))
    scored.sort(key=lambda p: p[0], reverse=True)

    return [
        {
            "id": row["id"],
            "score": round(score, 3),
            "project_id": row["project_id"],
            "title": row["title"],
            "body": row["body"],
            "meta": json.loads(row["meta"]),
            "created_at": row["created_at"],
        }
        for score, row in scored[:top_k]
        if score >= min_score
    ]


# ======================================================================
# Public async API
# ======================================================================
async def record_adr(project_id: str, title: str, body: str, meta: dict[str, Any] | None = None) -> int:
    return await asyncio.to_thread(_insert_document, "adr", project_id, title, body, meta or {})


async def record_incident(
    project_id: str, symptom: str, fix: str, meta: dict[str, Any] | None = None
) -> int:
    """One diagnosed failure: what it looked like + what resolved it."""
    meta = {**(meta or {}), "fix": fix}
    title = symptom.strip().splitlines()[0][:120] if symptom.strip() else "incident"
    body = f"SYMPTOM:\n{symptom}\n\nFIX:\n{fix}"
    return await asyncio.to_thread(_insert_document, "incident", project_id, title, body, meta)


async def record_exemplar(
    project_id: str, category: str, title: str, body: str, meta: dict[str, Any] | None = None
) -> int:
    """A first-try-review-pass piece of work, kept as future few-shot context."""
    meta = {**(meta or {}), "category": category}
    return await asyncio.to_thread(_insert_document, "exemplar", project_id, title, body, meta)


async def search(kind: str, query: str, top_k: int = 3, min_score: float = 0.1) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    return await asyncio.to_thread(_search, kind, query, top_k, min_score)


async def record_outcome(
    project_id: str,
    ticket: dict[str, Any],
    category: str,
    agent: str,
    stage: str,
    result: str,
    revision_rounds: int = 0,
    detail: str = "",
) -> None:
    def _write() -> None:
        with _connect() as conn:
            _init(conn)
            conn.execute(
                "INSERT INTO ticket_outcomes (project_id, ticket_id, ticket_title, category, agent, stage, "
                "result, revision_rounds, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    str(ticket.get("id", "")),
                    str(ticket.get("title", ""))[:200],
                    category,
                    agent,
                    stage,
                    result,
                    revision_rounds,
                    detail[:2000],
                    time.time(),
                ),
            )

    await asyncio.to_thread(_write)


async def failure_rate(category: str, window: int = 20) -> tuple[int, int]:
    """(failures, total) over the last `window` outcomes in this category —
    the signal the risk tiers use to auto-downgrade autonomy."""

    def _query() -> tuple[int, int]:
        with _connect() as conn:
            _init(conn)
            rows = conn.execute(
                "SELECT result FROM ticket_outcomes WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, window),
            ).fetchall()
        results = [r["result"] for r in rows]
        return results.count("fail"), len(results)

    return await asyncio.to_thread(_query)


async def agent_track_record(agent: str) -> dict[str, dict[str, Any]]:
    """Per-category pass/fail/avg-revision stats for one agent, from the ledger."""

    def _query() -> dict[str, dict[str, Any]]:
        with _connect() as conn:
            _init(conn)
            rows = conn.execute(
                "SELECT category, result, COUNT(*) AS n, AVG(revision_rounds) AS avg_rounds "
                "FROM ticket_outcomes WHERE agent = ? GROUP BY category, result",
                (agent,),
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            cat = out.setdefault(r["category"], {"pass": 0, "fail": 0, "avg_rounds": 0.0})
            cat[r["result"]] = r["n"]
            cat["avg_rounds"] = round(float(r["avg_rounds"] or 0), 2)
        return out

    return await asyncio.to_thread(_query)


async def recent_exemplars(category: str, limit: int = 2) -> list[dict[str, Any]]:
    """Newest exemplars in a category — injected as few-shot context for the
    Build pool (prompt-level learning on top of fixed Groq models)."""

    def _query() -> list[dict[str, Any]]:
        with _connect() as conn:
            _init(conn)
            rows = conn.execute(
                "SELECT id, title, body, meta, created_at FROM documents "
                "WHERE kind = 'exemplar' AND json_extract(meta, '$.category') = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        return [
            {"id": r["id"], "title": r["title"], "body": r["body"], "meta": json.loads(r["meta"])}
            for r in rows
        ]

    return await asyncio.to_thread(_query)
