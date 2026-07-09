"""Task Management Engine — the one place task business-rules live.

Both the REST API and the Task Agent go through here, never straight to the
store, so validation, reminder materialization, due-queue arming, and
recurrence all behave identically no matter who asked. The engine owns the
invariant "every pending reminder in SQLite is armed in the Redis due-queue".
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.tasks import duequeue, store
from app.tasks.recurrence import is_valid_rrule, next_occurrence
from app.tasks.timeutil import UTC, local_to_utc_iso, now_iso, parse_iso, to_iso

log = logging.getLogger("asterion.tasks.engine")


class TaskValidationError(ValueError):
    """A task payload was rejected before touching the store."""


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------
def _normalize_due(due: str | None, tz: str, has_time: bool | None) -> tuple[str | None, bool]:
    """Turn a wall-clock (or offset-carrying) due string into UTC ISO.

    Returns (due_at_utc_iso | None, due_has_time). A bare ``YYYY-MM-DD`` is an
    all-day task (has_time False); anything with a clock component is timed.
    """
    if not due:
        return None, False
    txt = due.strip()
    inferred_time = "T" in txt or " " in txt.strip()
    utc = local_to_utc_iso(txt, tz)
    if utc is None:
        raise TaskValidationError(f"couldn't understand the due date/time: {due!r}")
    resolved_has_time = has_time if has_time is not None else inferred_time
    return utc, bool(resolved_has_time)


def _materialize_reminders(due_at: str | None, specs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Compute absolute fire times from the due time and each reminder's lead."""
    if not due_at or not specs:
        return []
    base = parse_iso(due_at)
    if base is None:
        return []
    out = []
    for spec in specs:
        offset = int(spec.get("offset_min", 0) or 0)
        fire = base - timedelta(minutes=offset)
        out.append({
            "offset_min": offset,
            "channel": spec.get("channel", "inapp"),
            "fire_at": to_iso(fire.astimezone(UTC)),
        })
    return out


def _default_reminder_specs(due_at: str | None, has_time: bool) -> list[dict[str, Any]]:
    """When the caller gives a due time but no explicit reminders, arm one at
    the due moment — a reminder people expect to actually fire."""
    if not due_at:
        return []
    return [{"offset_min": 0, "channel": "inapp"}]


# ---------------------------------------------------------------------------
# create / update / lifecycle
# ---------------------------------------------------------------------------
async def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    title = (payload.get("title") or "").strip()
    if not title:
        raise TaskValidationError("a task needs a title")
    if payload.get("priority") and payload["priority"] not in store.PRIORITIES:
        raise TaskValidationError(f"priority must be one of {store.PRIORITIES}")

    tz = payload.get("timezone") or "UTC"
    recurrence = payload.get("recurrence") or None
    if recurrence and not is_valid_rrule(recurrence):
        raise TaskValidationError(f"unsupported recurrence rule: {recurrence!r}")

    due_at, has_time = _normalize_due(payload.get("due"), tz, payload.get("due_has_time"))
    reminder_specs = payload.get("reminders")
    if reminder_specs is None:
        reminder_specs = _default_reminder_specs(due_at, has_time)
    reminders = _materialize_reminders(due_at, reminder_specs)

    data = {
        "title": title[:300],
        "description": (payload.get("description") or "")[:5000],
        "status": "open",
        "priority": payload.get("priority") or "normal",
        "due_at": due_at,
        "due_has_time": has_time,
        "timezone": tz,
        "recurrence": recurrence,
        "category_id": payload.get("category_id"),
        "chat_id": payload.get("chat_id"),
        "source": payload.get("source", "manual"),
        "user_id": payload.get("user_id", "local"),
        "actor": payload.get("actor", "user"),
    }
    tid = await store.create_task(data, tags=payload.get("tags") or [], reminders=reminders)
    await duequeue.arm_many(await _reminder_rows(tid))
    task = await store.get_task(tid)
    log.info("task created %s (%s) due=%s recur=%s", tid, title[:40], due_at, recurrence)
    return task  # type: ignore[return-value]


async def _reminder_rows(tid: str) -> list[dict[str, Any]]:
    task = await store.get_task(tid)
    return (task or {}).get("reminders", [])


async def update_task(tid: str, fields: dict[str, Any], actor: str = "user") -> dict[str, Any] | None:
    existing = await store.get_task(tid)
    if existing is None:
        return None

    patch: dict[str, Any] = {}
    for k in ("title", "description", "priority", "category_id", "tags"):
        if k in fields:
            patch[k] = fields[k]
    if "status" in fields and fields["status"] in store.STATUSES:
        patch["status"] = fields["status"]

    tz = fields.get("timezone") or existing.get("timezone") or "UTC"
    reschedule = "due" in fields or "recurrence" in fields or "reminders" in fields
    new_due = existing.get("due_at")
    if "due" in fields:
        patch["timezone"] = tz
        new_due, has_time = _normalize_due(fields.get("due"), tz, fields.get("due_has_time"))
        patch["due_at"] = new_due
        patch["due_has_time"] = has_time
    if "recurrence" in fields:
        rr = fields["recurrence"] or None
        if rr and not is_valid_rrule(rr):
            raise TaskValidationError(f"unsupported recurrence rule: {rr!r}")
        patch["recurrence"] = rr

    updated = await store.update_task(tid, patch, actor=actor)
    if updated is None:
        return None

    if reschedule:
        # disarm the old reminders, materialize + arm fresh ones
        await duequeue.disarm(*[r["id"] for r in existing.get("reminders", [])])
        specs = fields.get("reminders")
        if specs is None:
            specs = [{"offset_min": r["offset_min"], "channel": r["channel"]} for r in existing.get("reminders", [])] \
                or _default_reminder_specs(updated.get("due_at"), updated.get("due_has_time", False))
        reminders = _materialize_reminders(updated.get("due_at"), specs)
        rows = await store.replace_reminders(tid, reminders)
        await duequeue.arm_many(rows)
        await store.update_task(tid, {"_event": "rescheduled"}, actor=actor)
        updated = await store.get_task(tid)
    return updated


async def complete_task(tid: str, actor: str = "user") -> dict[str, Any] | None:
    existing = await store.get_task(tid)
    if existing is None:
        return None
    await duequeue.disarm(*[r["id"] for r in existing.get("reminders", [])])
    updated = await store.update_task(
        tid, {"status": "done", "completed_at": now_iso(), "_event": "completed"}, actor=actor
    )
    # Recurring task: spawn the next concrete occurrence.
    if existing.get("recurrence") and existing.get("due_at"):
        await _spawn_next_occurrence(existing, actor="scheduler" if actor == "scheduler" else "user")
    return updated


async def cancel_task(tid: str, actor: str = "user") -> dict[str, Any] | None:
    existing = await store.get_task(tid)
    if existing is None:
        return None
    await duequeue.disarm(*[r["id"] for r in existing.get("reminders", [])])
    return await store.update_task(tid, {"status": "cancelled", "_event": "cancelled"}, actor=actor)


async def delete_task(tid: str) -> bool:
    existing = await store.get_task(tid)
    if existing:
        await duequeue.disarm(*[r["id"] for r in existing.get("reminders", [])])
    return await store.delete_task(tid)


async def mark_missed(tid: str) -> dict[str, Any] | None:
    existing = await store.get_task(tid)
    if existing is None:
        return None
    await duequeue.disarm(*[r["id"] for r in existing.get("reminders", [])])
    return await store.update_task(tid, {"status": "missed", "_event": "missed"}, actor="scheduler")


async def _spawn_next_occurrence(prev: dict[str, Any], actor: str = "scheduler") -> dict[str, Any] | None:
    """Create the following occurrence of a recurring task after it completes
    or is expanded by the scheduler."""
    nxt = next_occurrence(prev["recurrence"], prev["due_at"], prev.get("timezone", "UTC"))
    if not nxt:
        return None
    specs = [{"offset_min": r["offset_min"], "channel": r["channel"]} for r in prev.get("reminders", [])] \
        or _default_reminder_specs(nxt, prev.get("due_has_time", True))
    reminders = _materialize_reminders(nxt, specs)
    data = {
        "title": prev["title"],
        "description": prev.get("description", ""),
        "status": "open",
        "priority": prev.get("priority", "normal"),
        "due_at": nxt,
        "due_has_time": prev.get("due_has_time", True),
        "timezone": prev.get("timezone", "UTC"),
        "recurrence": prev["recurrence"],
        "category_id": prev.get("category_id"),
        "chat_id": prev.get("chat_id"),
        "source": "recurrence",
        "user_id": prev.get("user_id", "local"),
        "actor": actor,
    }
    tid = await store.create_task(data, tags=prev.get("tags", []), reminders=reminders)
    await duequeue.arm_many(await _reminder_rows(tid))
    log.info("recurrence spawned %s → next %s", prev["id"], nxt)
    return await store.get_task(tid)
