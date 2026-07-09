"""Ticket board endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.orchestration import runner
from app.services import project_store as store

router = APIRouter(tags=["tickets"])


@router.get("/projects/{pid}/tickets")
async def get_tickets(pid: str) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    snap = await runner.snapshot(pid)
    values = snap.values if snap else {}
    return {
        "tickets": values.get("tickets", []),
        "current_ticket_index": values.get("current_ticket_index", 0),
    }
