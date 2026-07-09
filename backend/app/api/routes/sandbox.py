"""Sandbox endpoints: run commands in the project workspace, tail logs live,
manage sessions. The execution rules (isolation, limits, deny-list) live in
`app.sandbox.service`."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import SandboxRunRequest
from app.sandbox import service
from app.services import project_store as store

router = APIRouter(tags=["sandbox"])


async def _require_project(pid: str) -> None:
    # Accept the project if its workspace exists on disk even when the (volatile
    # fakeredis) metadata was dropped by a restart — the sandbox operates on
    # those same on-disk files, so this keeps Files and Sandbox linked.
    if not await store.project_exists(pid):
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{pid}/sandbox/run")
async def run(pid: str, req: SandboxRunRequest) -> dict:
    await _require_project(pid)
    try:
        session = await service.start(pid, req.command, timeout_s=req.timeout_s, background=req.background)
    except service.SandboxDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"session": session.snapshot()}


@router.get("/projects/{pid}/sandbox/sessions")
async def sessions(pid: str) -> dict:
    await _require_project(pid)
    return {"sessions": await service.list_sessions(pid)}


@router.post("/projects/{pid}/sandbox/kill/{sid}")
async def kill(pid: str, sid: str) -> dict:
    await _require_project(pid)
    if not await service.kill(pid, sid):
        raise HTTPException(status_code=409, detail="session is not running")
    return {"ok": True}


@router.get("/projects/{pid}/sandbox/stream")
async def stream(pid: str, request: Request):
    """SSE tail of the project's sandbox log (all sessions interleaved).
    Honors Last-Event-ID so reconnects resume instead of replaying."""
    await _require_project(pid)
    start_id = request.headers.get("last-event-id") or request.query_params.get("from") or "0-0"

    async def gen():
        last_id = start_id
        while True:
            if await request.is_disconnected():
                break
            batch = await service.read_log(pid, last_id, block_ms=15000)
            if not batch:
                await asyncio.sleep(0.4)  # fakeredis may not block
                continue
            for entry_id, entry in batch:
                last_id = entry_id
                yield {"event": "log", "id": entry_id, "data": json.dumps(entry)}

    return EventSourceResponse(gen())
