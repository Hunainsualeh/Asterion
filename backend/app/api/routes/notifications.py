"""Notification center REST + live SSE stream.

Mirrors app/api/routes/stream.py: the SSE endpoint tails the user's global
notification stream and honors Last-Event-ID so a reconnect resumes cleanly.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.notifications import service

router = APIRouter(tags=["notifications"])

USER = "local"


class ReadBody(BaseModel):
    id: str = "all"  # a stream id, or "all"


@router.get("/notifications")
async def list_notifications(limit: int = 50) -> dict:
    items = await service.list_notifications(USER, min(max(limit, 1), 200))
    return {"notifications": items, "unread": await service.unread_count(USER)}


@router.post("/notifications/read")
async def mark_read(body: ReadBody) -> dict:
    unread = await service.mark_read(USER, body.id)
    return {"ok": True, "unread": unread}


@router.get("/notifications/events")
async def notification_events(request: Request):
    # Default to "$" (only new events) unless the client asks to replay.
    start_id = request.headers.get("last-event-id") or request.query_params.get("from") or "$"

    async def event_generator():
        last_id = start_id
        while True:
            if await request.is_disconnected():
                break
            batch = await service.read_stream(USER, last_id, block_ms=15000, count=50)
            if not batch:
                await asyncio.sleep(0.4)  # fakeredis may not block
                continue
            for entry_id, ev in batch:
                last_id = entry_id
                yield {"event": "notification", "id": entry_id, "data": json.dumps(ev)}

    return EventSourceResponse(event_generator())
