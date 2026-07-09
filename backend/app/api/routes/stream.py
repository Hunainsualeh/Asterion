"""Server-Sent Events stream of pipeline progress.

Tails the project's Redis event stream and forwards each event to the browser.
A late-connecting client replays from the beginning (`?from=0`) by default.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.orchestration.events import read_events

router = APIRouter(tags=["stream"])


@router.get("/projects/{pid}/events")
async def events(pid: str, request: Request):
    # Honor Last-Event-ID (sent automatically by EventSource on reconnect) so
    # a dropped connection resumes where it left off instead of replaying the
    # whole history — replays were duplicating every chat bubble client-side.
    start_id = request.headers.get("last-event-id") or request.query_params.get("from") or "0-0"

    async def event_generator():
        last_id = start_id
        while True:
            if await request.is_disconnected():
                break
            batch = await read_events(pid, last_id, block_ms=15000, count=100)
            if not batch:
                # fakeredis may not block; avoid a busy loop.
                await asyncio.sleep(0.4)
                continue
            for entry_id, ev in batch:
                last_id = entry_id
                # The stream entry id doubles as a globally unique event id —
                # clients dedupe on it and EventSource echoes it on reconnect.
                yield {"event": ev.get("kind", "message"), "id": entry_id, "data": json.dumps({**ev, "id": entry_id})}

    return EventSourceResponse(event_generator())
