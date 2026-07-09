"""Notification store + publish + live tail.

A per-user Redis Stream holds notifications (replayable, capped). An unread
counter drives the bell badge; a read-watermark + a small read-set implement
"mark one / mark all read" without scanning the whole stream. The SSE endpoint
tails the stream exactly like the pipeline event stream, so a client that
connects late replays from Last-Event-ID.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.redis.client import get_redis, key

log = logging.getLogger("asterion.notifications")

MAXLEN = 500


def _stream_key(user: str) -> str:
    return key("notify", user)


def _unread_key(user: str) -> str:
    return key("notify", "unread", user)


def _watermark_key(user: str) -> str:
    return key("notify", "watermark", user)


def _readset_key(user: str) -> str:
    return key("notify", "readset", user)


async def notify(
    user: str,
    kind: str,
    title: str,
    body: str = "",
    *,
    task_id: str | None = None,
    action: dict[str, Any] | None = None,
    tone: str = "info",
) -> dict[str, Any]:
    """Publish a notification to the user's feed. Returns the stored event."""
    r = await get_redis()
    event = {
        "nid": uuid.uuid4().hex[:12],
        "kind": kind,               # reminder | missed | task | system
        "title": title,
        "body": body,
        "task_id": task_id or "",
        "action": action or {},     # optional client action (e.g. open task)
        "tone": tone,               # info | success | warning | error
        "ts": time.time(),
    }
    entry_id = await r.xadd(_stream_key(user), {"e": json.dumps(event)}, maxlen=MAXLEN, approximate=True)
    entry_id = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
    event["id"] = entry_id
    await r.incr(_unread_key(user))
    log.info("notify[%s] %s: %s", user, kind, title)
    return event


async def unread_count(user: str) -> int:
    r = await get_redis()
    raw = await r.get(_unread_key(user))
    if raw is None:
        return 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


async def list_notifications(user: str, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent notifications, newest first, annotated with read state."""
    r = await get_redis()
    raw = await r.xrevrange(_stream_key(user), count=limit)
    watermark = await r.get(_watermark_key(user))
    watermark = watermark.decode() if isinstance(watermark, bytes) else (watermark or "0-0")
    read_ids = {m.decode() if isinstance(m, bytes) else m for m in await r.smembers(_readset_key(user))}

    out: list[dict[str, Any]] = []
    for entry_id, fields in raw:
        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        payload = fields.get(b"e") or fields.get("e")
        if isinstance(payload, bytes):
            payload = payload.decode()
        try:
            ev = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        ev["id"] = eid
        ev["read"] = _entry_le(eid, watermark) or eid in read_ids
        out.append(ev)
    return out


async def mark_read(user: str, nid_or_all: str) -> int:
    """Mark a single stream-id read, or all. Returns the new unread count."""
    r = await get_redis()
    if nid_or_all == "all":
        last = await r.xrevrange(_stream_key(user), count=1)
        if last:
            last_id = last[0][0]
            last_id = last_id.decode() if isinstance(last_id, bytes) else last_id
            await r.set(_watermark_key(user), last_id)
        await r.delete(_readset_key(user))
        await r.set(_unread_key(user), 0)
        return 0
    await r.sadd(_readset_key(user), nid_or_all)
    # decrement, floored at 0
    current = await unread_count(user)
    new_val = max(0, current - 1)
    await r.set(_unread_key(user), new_val)
    return new_val


def _entry_le(a: str, b: str) -> bool:
    """True if stream id a <= b (ms-seq ordering)."""
    def parse(x: str) -> tuple[int, int]:
        try:
            ms, seq = x.split("-")
            return int(ms), int(seq)
        except (ValueError, AttributeError):
            return (0, 0)
    return parse(a) <= parse(b)


async def read_stream(user: str, last_id: str = "$", block_ms: int = 15000, count: int = 50):
    """Blocking tail of the notification stream for the SSE endpoint."""
    r = await get_redis()
    result = await r.xread({_stream_key(user): last_id}, count=count, block=block_ms)
    out: list[tuple[str, dict[str, Any]]] = []
    if result:
        for _stream, entries in result:
            for entry_id, fields in entries:
                raw = fields.get(b"e") or fields.get("e")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                ev["id"] = eid
                out.append((eid, ev))
    return out
