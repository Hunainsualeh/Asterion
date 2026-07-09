"""The Task Store — durable, queryable, independent of the chat pipeline.

SQLite (stdlib, no infra), mirroring app/knowledge/store.py: a fresh WAL-mode
connection per call, run in a thread via asyncio.to_thread. The schema is the
source of truth for tasks; Redis only holds a rebuildable hot-index of due
reminders (see duequeue.py).

Design point that satisfies "tasks remain accessible even if chats are
deleted": chat_id is a plain nullable column, NOT a foreign key into the
volatile Redis project store — deleting a chat never touches tasks.db.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from typing import Any

from app.config import BACKEND_DIR
from app.tasks.timeutil import now_iso

log = logging.getLogger("asterion.tasks.store")

DB_PATH = BACKEND_DIR / "tasks.db"

STATUSES = ("open", "in_progress", "done", "missed", "cancelled")
PRIORITIES = ("low", "normal", "high", "urgent")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'local',
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',
    priority      TEXT NOT NULL DEFAULT 'normal',
    due_at        TEXT,
    due_has_time  INTEGER NOT NULL DEFAULT 0,
    timezone      TEXT NOT NULL DEFAULT 'UTC',
    recurrence    TEXT,
    category_id   TEXT,
    chat_id       TEXT,
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(user_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(user_id, status);

CREATE TABLE IF NOT EXISTS categories (
    id      TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    name    TEXT NOT NULL,
    color   TEXT NOT NULL DEFAULT '#0E7C86',
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY(task_id, tag)
);

CREATE TABLE IF NOT EXISTS reminders (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    offset_min INTEGER NOT NULL DEFAULT 0,
    channel    TEXT NOT NULL DEFAULT 'inapp',
    fire_at    TEXT NOT NULL,
    fired_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(fire_at) WHERE fired_at IS NULL;

CREATE TABLE IF NOT EXISTS task_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    actor    TEXT NOT NULL DEFAULT 'user',
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id);

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
"""

SCHEMA_VERSION = "1"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (SCHEMA_VERSION,),
    )


def init_sync() -> None:
    """Create the DB/schema eagerly (called once at startup)."""
    with _connect() as conn:
        _init(conn)


async def init() -> None:
    await asyncio.to_thread(init_sync)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# row → dict
# ---------------------------------------------------------------------------
def _task_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    tags = [r["tag"] for r in conn.execute("SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (row["id"],))]
    reminders = [
        dict(r) for r in conn.execute(
            "SELECT id, offset_min, channel, fire_at, fired_at FROM reminders WHERE task_id=? ORDER BY fire_at",
            (row["id"],),
        )
    ]
    d = dict(row)
    d["due_has_time"] = bool(d.get("due_has_time"))
    d["tags"] = tags
    d["reminders"] = reminders
    return d


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------
def _create_task(data: dict[str, Any], tags: list[str], reminders: list[dict[str, Any]]) -> str:
    tid = data.get("id") or new_id("task")
    now = now_iso()
    with _connect() as conn:
        _init(conn)
        conn.execute(
            "INSERT INTO tasks (id, user_id, title, description, status, priority, due_at, due_has_time, "
            "timezone, recurrence, category_id, chat_id, source, created_at, updated_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                tid,
                data.get("user_id", "local"),
                data["title"],
                data.get("description", ""),
                data.get("status", "open"),
                data.get("priority", "normal"),
                data.get("due_at"),
                1 if data.get("due_has_time") else 0,
                data.get("timezone", "UTC"),
                data.get("recurrence"),
                data.get("category_id"),
                data.get("chat_id"),
                data.get("source", "manual"),
                now,
                now,
            ),
        )
        for tag in {t.strip() for t in tags if t.strip()}:
            conn.execute("INSERT OR IGNORE INTO task_tags(task_id, tag) VALUES(?,?)", (tid, tag))
        for rem in reminders:
            conn.execute(
                "INSERT INTO reminders(id, task_id, offset_min, channel, fire_at, fired_at) "
                "VALUES(?,?,?,?,?,NULL)",
                (new_id("rem"), tid, int(rem.get("offset_min", 0)), rem.get("channel", "inapp"), rem["fire_at"]),
            )
        conn.execute(
            "INSERT INTO task_events(task_id, ts, kind, actor, detail) VALUES(?,?,?,?,?)",
            (tid, now, "created", data.get("actor", "user"), json.dumps({"title": data["title"]})),
        )
    return tid


def _get_task(tid: str) -> dict[str, Any] | None:
    with _connect() as conn:
        _init(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _task_dict(conn, row) if row else None


def _list_tasks(filters: dict[str, Any]) -> list[dict[str, Any]]:
    where = ["user_id = ?"]
    args: list[Any] = [filters.get("user_id", "local")]
    if filters.get("status"):
        statuses = filters["status"] if isinstance(filters["status"], list) else [filters["status"]]
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        args += statuses
    if filters.get("priority"):
        where.append("priority = ?")
        args.append(filters["priority"])
    if filters.get("category_id"):
        where.append("category_id = ?")
        args.append(filters["category_id"])
    if filters.get("due_from"):
        where.append("due_at >= ?")
        args.append(filters["due_from"])
    if filters.get("due_to"):
        where.append("due_at <= ?")
        args.append(filters["due_to"])
    if filters.get("q"):
        where.append("(title LIKE ? OR description LIKE ?)")
        args += [f"%{filters['q']}%", f"%{filters['q']}%"]

    sql = (
        "SELECT * FROM tasks WHERE " + " AND ".join(where)
        + " ORDER BY (due_at IS NULL), due_at ASC, "
        + "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC"
    )
    limit = int(filters.get("limit", 200))
    sql += " LIMIT ?"
    args.append(limit)

    with _connect() as conn:
        _init(conn)
        rows = conn.execute(sql, args).fetchall()
        results = [_task_dict(conn, r) for r in rows]

    if filters.get("tag"):
        tag = filters["tag"]
        results = [t for t in results if tag in t["tags"]]
    return results


def _update_task(tid: str, fields: dict[str, Any], actor: str) -> dict[str, Any] | None:
    allowed = {
        "title", "description", "status", "priority", "due_at", "due_has_time",
        "timezone", "recurrence", "category_id", "completed_at",
    }
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            args.append(1 if (k == "due_has_time" and v) else (0 if k == "due_has_time" else v))
    now = now_iso()
    with _connect() as conn:
        _init(conn)
        if conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone() is None:
            return None
        if sets:
            sets.append("updated_at = ?")
            args.append(now)
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*args, tid))
        if "tags" in fields and isinstance(fields["tags"], list):
            conn.execute("DELETE FROM task_tags WHERE task_id=?", (tid,))
            for tag in {t.strip() for t in fields["tags"] if t.strip()}:
                conn.execute("INSERT OR IGNORE INTO task_tags(task_id, tag) VALUES(?,?)", (tid, tag))
        conn.execute(
            "INSERT INTO task_events(task_id, ts, kind, actor, detail) VALUES(?,?,?,?,?)",
            (tid, now, fields.get("_event", "updated"), actor, json.dumps({k: v for k, v in fields.items() if not k.startswith("_")}, default=str)),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _task_dict(conn, row)


def _delete_task(tid: str) -> bool:
    with _connect() as conn:
        _init(conn)
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
        # task_tags / reminders cascade; task_events kept as tombstone-free history is fine to drop too
        conn.execute("DELETE FROM task_events WHERE task_id=?", (tid,))
        return cur.rowcount > 0


def _task_events(tid: str, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT ts, kind, actor, detail FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (tid, limit),
        ).fetchall()
    return [{"ts": r["ts"], "kind": r["kind"], "actor": r["actor"],
             "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]


# ---------------------------------------------------------------------------
# reminders (materialized rows; the ZSET is the live index over these)
# ---------------------------------------------------------------------------
def _replace_reminders(tid: str, reminders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with _connect() as conn:
        _init(conn)
        conn.execute("DELETE FROM reminders WHERE task_id=?", (tid,))
        out = []
        for rem in reminders:
            rid = new_id("rem")
            conn.execute(
                "INSERT INTO reminders(id, task_id, offset_min, channel, fire_at, fired_at) VALUES(?,?,?,?,?,NULL)",
                (rid, tid, int(rem.get("offset_min", 0)), rem.get("channel", "inapp"), rem["fire_at"]),
            )
            out.append({"id": rid, "task_id": tid, "fire_at": rem["fire_at"],
                        "offset_min": int(rem.get("offset_min", 0)), "channel": rem.get("channel", "inapp")})
        return out


def _pending_reminders() -> list[dict[str, Any]]:
    """All reminders that haven't fired yet — used to rehydrate the ZSET."""
    with _connect() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT r.id, r.task_id, r.fire_at, r.channel FROM reminders r "
            "JOIN tasks t ON t.id = r.task_id "
            "WHERE r.fired_at IS NULL AND t.status IN ('open','in_progress')"
        ).fetchall()
    return [dict(r) for r in rows]


def _get_reminder(rid: str) -> dict[str, Any] | None:
    with _connect() as conn:
        _init(conn)
        row = conn.execute(
            "SELECT r.*, t.title AS task_title, t.status AS task_status, t.priority AS task_priority, "
            "t.user_id AS user_id, t.due_at AS due_at FROM reminders r "
            "JOIN tasks t ON t.id = r.task_id WHERE r.id=?",
            (rid,),
        ).fetchone()
        return dict(row) if row else None


def _mark_reminder_fired(rid: str) -> None:
    with _connect() as conn:
        _init(conn)
        conn.execute("UPDATE reminders SET fired_at=? WHERE id=?", (now_iso(), rid))


def _sweepable_overdue(cutoff_iso: str) -> list[dict[str, Any]]:
    """Open tasks whose due time (plus grace) has passed — candidates for missed."""
    with _connect() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') "
            "AND due_at IS NOT NULL AND due_at < ? AND recurrence IS NULL",
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def _due_recurring(now_is: str) -> list[dict[str, Any]]:
    """Recurring tasks whose current occurrence is due/past — expand the next."""
    with _connect() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') "
            "AND recurrence IS NOT NULL AND due_at IS NOT NULL AND due_at <= ?",
            (now_is,),
        ).fetchall()
        return [_task_dict(conn, r) for r in rows]


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------
def _list_categories(user_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        _init(conn)
        rows = conn.execute("SELECT * FROM categories WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def _create_category(user_id: str, name: str, color: str) -> dict[str, Any]:
    cid = new_id("cat")
    with _connect() as conn:
        _init(conn)
        conn.execute(
            "INSERT INTO categories(id, user_id, name, color) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET color=excluded.color",
            (cid, user_id, name.strip()[:60], color),
        )
        row = conn.execute("SELECT * FROM categories WHERE user_id=? AND name=?", (user_id, name.strip()[:60])).fetchone()
        return dict(row)


def _delete_category(cid: str) -> bool:
    with _connect() as conn:
        _init(conn)
        conn.execute("UPDATE tasks SET category_id=NULL WHERE category_id=?", (cid,))
        cur = conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        return cur.rowcount > 0


def _summary(user_id: str) -> dict[str, Any]:
    with _connect() as conn:
        _init(conn)
        counts = {s: 0 for s in STATUSES}
        for r in conn.execute("SELECT status, COUNT(*) n FROM tasks WHERE user_id=? GROUP BY status", (user_id,)):
            counts[r["status"]] = r["n"]
        now = now_iso()
        upcoming = [
            _task_dict(conn, r) for r in conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status IN ('open','in_progress') "
                "AND due_at IS NOT NULL AND due_at >= ? ORDER BY due_at LIMIT 8",
                (user_id, now),
            )
        ]
        overdue = [
            _task_dict(conn, r) for r in conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status IN ('open','in_progress') "
                "AND due_at IS NOT NULL AND due_at < ? ORDER BY due_at DESC LIMIT 8",
                (user_id, now),
            )
        ]
    return {"counts": counts, "upcoming": upcoming, "overdue": overdue}


# ======================================================================
# Public async API
# ======================================================================
async def create_task(data: dict[str, Any], tags: list[str] | None = None,
                      reminders: list[dict[str, Any]] | None = None) -> str:
    return await asyncio.to_thread(_create_task, data, tags or [], reminders or [])


async def get_task(tid: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_task, tid)


async def list_tasks(filters: dict[str, Any]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_tasks, filters)


async def update_task(tid: str, fields: dict[str, Any], actor: str = "user") -> dict[str, Any] | None:
    return await asyncio.to_thread(_update_task, tid, fields, actor)


async def delete_task(tid: str) -> bool:
    return await asyncio.to_thread(_delete_task, tid)


async def task_events(tid: str, limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_task_events, tid, limit)


async def replace_reminders(tid: str, reminders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_replace_reminders, tid, reminders)


async def pending_reminders() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_pending_reminders)


async def get_reminder(rid: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_reminder, rid)


async def mark_reminder_fired(rid: str) -> None:
    await asyncio.to_thread(_mark_reminder_fired, rid)


async def sweepable_overdue(cutoff_iso: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_sweepable_overdue, cutoff_iso)


async def due_recurring(now_is: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_due_recurring, now_is)


async def list_categories(user_id: str = "local") -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_categories, user_id)


async def create_category(name: str, color: str = "#0E7C86", user_id: str = "local") -> dict[str, Any]:
    return await asyncio.to_thread(_create_category, user_id, name, color)


async def delete_category(cid: str) -> bool:
    return await asyncio.to_thread(_delete_category, cid)


async def summary(user_id: str = "local") -> dict[str, Any]:
    return await asyncio.to_thread(_summary, user_id)
