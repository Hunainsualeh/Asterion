"""Lightweight project registry in Redis.

Tracks each project's idea/status and the current pending interrupt (the gate
the pipeline is waiting on), so the API can answer status queries without
re-running the graph.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.config import get_settings
from app.redis.client import get_redis, key


def _project_key(pid: str) -> str:
    return key("project", pid)


def _pending_key(pid: str) -> str:
    return key("pending", pid)


def _projects_set() -> str:
    return key("projects")


def _decode(mapping: dict[bytes, bytes]) -> dict[str, str]:
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in mapping.items()
    }


async def create_project(pid: str, idea: str, lane: str = "project", intent: dict[str, Any] | None = None) -> None:
    r = await get_redis()
    # `title` defaults to a trimmed version of the idea so the sidebar never
    # shows a raw, possibly very long prompt; it's replaced with an
    # AI-generated one shortly after (see app.services.summarizer).
    fallback_title = idea.strip().splitlines()[0][:60]
    await r.hset(
        _project_key(pid),
        mapping={
            "idea": idea,
            "created_at": str(time.time()),
            "status": "starting",
            "title": fallback_title,
            "summary": "",
            "lane": lane,
            "intent": json.dumps(intent or {}),
        },
    )
    await r.sadd(_projects_set(), pid)


async def set_status(pid: str, status: str) -> None:
    r = await get_redis()
    await r.hset(_project_key(pid), "status", status)


async def set_title_summary(pid: str, title: str, summary: str) -> None:
    r = await get_redis()
    await r.hset(_project_key(pid), mapping={"title": title, "summary": summary})


async def rename_project(pid: str, title: str) -> None:
    r = await get_redis()
    await r.hset(_project_key(pid), "title", title.strip()[:120])


async def delete_project(pid: str) -> None:
    """Remove a project from the registry and drop all of its Redis state."""
    r = await get_redis()
    await r.srem(_projects_set(), pid)
    await r.delete(
        _project_key(pid),
        _pending_key(pid),
        _stage_key(pid),
        _result_key(pid),
        _clarify_key(pid),
        _history_key(pid),
        key("running", pid),
    )


async def set_lane(pid: str, lane: str, intent: dict[str, Any] | None = None) -> None:
    """Switch a project's execution lane (e.g. a chat that escalates into a
    full software project mid-conversation)."""
    r = await get_redis()
    mapping: dict[str, str] = {"lane": lane}
    if intent is not None:
        mapping["intent"] = json.dumps(intent)
    await r.hset(_project_key(pid), mapping=mapping)


async def get_project(pid: str) -> dict[str, str] | None:
    r = await get_redis()
    data = await r.hgetall(_project_key(pid))
    return _decode(data) if data else None


def workspace_exists(pid: str) -> bool:
    """True if this project's workspace directory is present on disk. The disk
    is the durable source of truth for a project's files; the Redis metadata is
    not (a native-Redis outage falls back to in-process fakeredis, wiped on
    restart). Guards against path traversal via a simple component check."""
    if not pid or "/" in pid or "\\" in pid or pid in (".", ".."):
        return False
    try:
        return (get_settings().workspace_dir / pid).is_dir()
    except OSError:
        return False


async def project_exists(pid: str) -> bool:
    """A project is usable if it's in the store OR its files exist on disk.
    The disk fallback keeps Files and Sandbox working (they operate on the same
    `workspace/<pid>` tree) even after a restart drops the fakeredis metadata."""
    if await get_project(pid) is not None:
        return True
    return workspace_exists(pid)


async def list_projects() -> list[dict[str, Any]]:
    r = await get_redis()
    pids = [p.decode() if isinstance(p, bytes) else p for p in await r.smembers(_projects_set())]
    out: list[dict[str, Any]] = []
    for pid in pids:
        proj = await get_project(pid)
        # Skip "ghost" hashes a still-running task can re-create after a delete:
        # only create_project writes created_at, so its absence means deleted.
        if proj and proj.get("created_at"):
            out.append({"project_id": pid, **proj, "running": await is_running(pid)})
    out.sort(key=lambda p: float(p.get("created_at", 0)), reverse=True)
    return out


# ---- pending interrupt (the gate we're waiting on) ----
async def set_pending(pid: str, interrupt_value: dict[str, Any]) -> None:
    r = await get_redis()
    # Every pause gets a unique id so the UI can tell "the same gate fired
    # again" (clarify loops, rejection loops) apart from "still the old
    # pause" — the composer re-enables on a new id even for a repeat gate.
    interrupt_value = {**interrupt_value, "interrupt_id": uuid.uuid4().hex[:12], "ts": time.time()}
    await r.set(_pending_key(pid), json.dumps(interrupt_value))


async def get_pending(pid: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(_pending_key(pid))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def clear_pending(pid: str) -> None:
    r = await get_redis()
    await r.delete(_pending_key(pid))


# ---- live stage (the friendly "what's happening right now" snapshot) ----
def _stage_key(pid: str) -> str:
    return key("stage", pid)


async def set_stage(pid: str, stage: dict[str, Any]) -> None:
    r = await get_redis()
    await r.set(_stage_key(pid), json.dumps(stage))


async def get_stage(pid: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(_stage_key(pid))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


# ---- final result (the task-lane deliverable / SDLC final report) ----
def _result_key(pid: str) -> str:
    return key("result", pid)


async def set_result(pid: str, result: dict[str, Any]) -> None:
    r = await get_redis()
    await r.set(_result_key(pid), json.dumps(result, default=str))


async def get_result(pid: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(_result_key(pid))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


# ---- pending clarification (task lane asked, waiting for the user) ----
def _clarify_key(pid: str) -> str:
    return key("clarify", pid)


async def set_clarify(pid: str, query: str) -> None:
    """Remember the original request while its clarifying questions are out;
    the next user message gets merged with it and runs immediately."""
    r = await get_redis()
    await r.set(_clarify_key(pid), json.dumps({"query": query, "ts": time.time()}))


async def get_clarify(pid: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(_clarify_key(pid))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def clear_clarify(pid: str) -> None:
    r = await get_redis()
    await r.delete(_clarify_key(pid))


# ---- conversation history (drives follow-up context in the task lane) ----
_HISTORY_MAX = 40          # turns kept per project
_HISTORY_TEXT_CAP = 4000   # chars stored per turn — enough context, bounded memory


def _history_key(pid: str) -> str:
    return key("history", pid)


async def append_history(pid: str, role: str, text: str) -> None:
    r = await get_redis()
    entry = json.dumps({"role": role, "text": text[:_HISTORY_TEXT_CAP], "ts": time.time()})
    await r.rpush(_history_key(pid), entry)
    await r.ltrim(_history_key(pid), -_HISTORY_MAX, -1)


async def get_history(pid: str, limit: int = 12) -> list[dict[str, Any]]:
    """Most recent `limit` turns, oldest first."""
    r = await get_redis()
    raw = await r.lrange(_history_key(pid), -limit, -1)
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, bytes):
            item = item.decode()
        try:
            out.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    return out


# ---- running flag (guards against concurrent resume) ----
async def set_running(pid: str, running: bool) -> None:
    r = await get_redis()
    if running:
        await r.set(key("running", pid), b"1")
    else:
        await r.delete(key("running", pid))


async def is_running(pid: str) -> bool:
    r = await get_redis()
    return bool(await r.exists(key("running", pid)))
