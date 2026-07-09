"""REST API for the task-management system.

Thin controllers over app.tasks.engine (writes) and app.tasks.store (reads).
Everything is scoped to a user_id — today the single implicit "local" user,
forward-compatible with real auth (a dependency can swap in the authenticated
id without touching these handlers).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tasks import engine, store
from app.tasks.engine import TaskValidationError

router = APIRouter(tags=["tasks"])

USER = "local"  # single-tenant for now; replace with an auth dependency later


class ReminderSpec(BaseModel):
    offset_min: int = 0
    channel: str = "inapp"


class TaskCreateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    due: str | None = None            # wall-clock ISO in `timezone`, or bare date
    due_has_time: bool | None = None
    timezone: str = "UTC"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    recurrence: str | None = None     # RRULE, e.g. "FREQ=WEEKLY;BYDAY=MO"
    reminders: list[ReminderSpec] | None = None
    tags: list[str] = Field(default_factory=list)
    category_id: str | None = None
    chat_id: str | None = None
    source: str = "manual"


class TaskUpdateBody(BaseModel):
    title: str | None = None
    description: str | None = None
    status: Literal["open", "in_progress", "done", "missed", "cancelled"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    due: str | None = None
    due_has_time: bool | None = None
    timezone: str | None = None
    recurrence: str | None = None
    reminders: list[ReminderSpec] | None = None
    tags: list[str] | None = None
    category_id: str | None = None


class CategoryBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str = "#0E7C86"


def _clean(body: BaseModel) -> dict[str, Any]:
    return {k: v for k, v in body.model_dump().items() if v is not None}


@router.post("/tasks")
async def create_task(body: TaskCreateBody) -> dict[str, Any]:
    payload = _clean(body)
    payload["user_id"] = USER
    if body.reminders is not None:
        payload["reminders"] = [r.model_dump() for r in body.reminders]
    try:
        return await engine.create_task(payload)
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    priority: str | None = None,
    tag: str | None = None,
    category_id: str | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"user_id": USER, "limit": min(max(limit, 1), 500)}
    if status:
        filters["status"] = status.split(",") if "," in status else status
    for k, v in (("priority", priority), ("tag", tag), ("category_id", category_id),
                 ("due_from", due_from), ("due_to", due_to), ("q", q)):
        if v:
            filters[k] = v
    return {"tasks": await store.list_tasks(filters)}


@router.get("/tasks/summary")
async def tasks_summary() -> dict[str, Any]:
    return await store.summary(USER)


@router.get("/tasks/{tid}")
async def get_task(tid: str) -> dict[str, Any]:
    task = await store.get_task(tid)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    task["events"] = await store.task_events(tid)
    return task


@router.patch("/tasks/{tid}")
async def update_task(tid: str, body: TaskUpdateBody) -> dict[str, Any]:
    fields = _clean(body)
    if body.reminders is not None:
        fields["reminders"] = [r.model_dump() for r in body.reminders]
    try:
        task = await engine.update_task(tid, fields)
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/tasks/{tid}/complete")
async def complete_task(tid: str) -> dict[str, Any]:
    task = await engine.complete_task(tid)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/tasks/{tid}/cancel")
async def cancel_task(tid: str) -> dict[str, Any]:
    task = await engine.cancel_task(tid)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.delete("/tasks/{tid}")
async def delete_task(tid: str) -> dict[str, Any]:
    if not await engine.delete_task(tid):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


# ---- categories ----
@router.get("/categories")
async def list_categories() -> dict[str, Any]:
    return {"categories": await store.list_categories(USER)}


@router.post("/categories")
async def create_category(body: CategoryBody) -> dict[str, Any]:
    return await store.create_category(body.name, body.color, USER)


@router.delete("/categories/{cid}")
async def delete_category(cid: str) -> dict[str, Any]:
    if not await store.delete_category(cid):
        raise HTTPException(status_code=404, detail="category not found")
    return {"ok": True}
