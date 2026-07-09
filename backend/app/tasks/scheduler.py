"""The background scheduler — the timer the whole assistant runs on.

A single asyncio loop, started from the FastAPI lifespan, that every tick:
  1. fires reminders whose time has arrived  (→ notifications)
  2. sweeps overdue open tasks to `missed`     (→ notifications)
  3. expands recurring tasks past their due    (→ next occurrence)

Restart-safety: on boot it rehydrates the Redis due-queue from SQLite, so the
documented uvicorn --reload restart (which kills this task) never loses a
pending reminder — the next process rebuilds the index and carries on.

Multi-worker-safety: each tick is guarded by a short Redis lock (SET NX EX), so
if the app is ever run with several workers only one actually ticks.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.notifications import channels, service as notifications
from app.redis.client import get_redis, key
from app.tasks import duequeue, engine, store
from app.tasks.recurrence import describe_rrule
from app.tasks.timeutil import now_iso, parse_iso, to_iso

log = logging.getLogger("asterion.tasks.scheduler")

TICK_SECONDS = 15
MISSED_GRACE_MIN = 2          # how long past due before an open task is "missed"
_LOCK_KEY_PARTS = ("sched", "lock")

_task: asyncio.Task | None = None
_stopping = asyncio.Event()


def start() -> None:
    """Launch the scheduler loop (idempotent)."""
    global _task
    if _task is not None and not _task.done():
        return
    _stopping.clear()
    _task = asyncio.create_task(_run(), name="task-scheduler")
    log.info("task scheduler started (tick=%ss)", TICK_SECONDS)


async def stop() -> None:
    _stopping.set()
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def _acquire_tick_lock() -> bool:
    r = await get_redis()
    # NX EX: only one holder at a time; expires so a crashed holder frees it.
    got = await r.set(key(*_LOCK_KEY_PARTS), b"1", nx=True, ex=TICK_SECONDS - 1)
    return bool(got)


async def _run() -> None:
    # Rehydrate the hot-index from durable storage before the first tick.
    try:
        await duequeue.rehydrate()
    except Exception:  # noqa: BLE001 — never let boot rehydrate kill the loop
        log.exception("due-queue rehydrate failed; continuing")

    while not _stopping.is_set():
        try:
            if await _acquire_tick_lock():
                await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must never stop the scheduler
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _tick() -> None:
    now = time.time()
    await _fire_due_reminders(now)
    await _sweep_missed()
    await _expand_recurrences()


async def _fire_due_reminders(now_epoch: float) -> None:
    ids = await duequeue.pop_due(now_epoch, limit=100)
    for rid in ids:
        rem = await store.get_reminder(rid)
        if rem is None or rem.get("fired_at"):
            continue
        if rem.get("task_status") not in ("open", "in_progress"):
            await store.mark_reminder_fired(rid)
            continue
        title = rem.get("task_title") or "Reminder"
        due = parse_iso(rem.get("due_at"))
        when = f" (due {_friendly_when(rem.get('due_at'))})" if due else ""
        # Route through the channel dispatcher so email/calendar/WhatsApp can be
        # added later without touching the scheduler.
        await channels.deliver(rem.get("channel", "inapp"), rem.get("user_id", "local"), {
            "kind": "reminder",
            "title": title,
            "body": f"Reminder{when}",
            "task_id": rem.get("task_id"),
            "action": {"action": "open_task", "task_id": rem.get("task_id")},
            "tone": "info",
        })
        await store.mark_reminder_fired(rid)
        await store.update_task(rem["task_id"], {"_event": "reminder_fired"}, actor="scheduler")


async def _sweep_missed() -> None:
    cutoff = to_iso(parse_iso(now_iso()))  # now, UTC
    # tasks due before (now - grace) that are still open → missed
    grace_cutoff = _minus_minutes(now_iso(), MISSED_GRACE_MIN)
    overdue = await store.sweepable_overdue(grace_cutoff)
    for task in overdue:
        updated = await engine.mark_missed(task["id"])
        if updated:
            await notifications.notify(
                task.get("user_id", "local"),
                kind="missed",
                title=f"Missed: {task['title']}",
                body="This task's time passed and it wasn't marked done.",
                task_id=task["id"],
                action={"action": "open_task", "task_id": task["id"]},
                tone="warning",
            )


async def _expand_recurrences() -> None:
    """A recurring task whose current occurrence is due but not completed:
    keep the timeline moving by rolling it to the next occurrence so a missed
    weekly reminder doesn't stall the whole series."""
    due = await store.due_recurring(now_iso())
    for task in due:
        # Only roll forward once its reminders have all fired (or it has none),
        # so we don't skip an occurrence the user could still act on.
        pending = [r for r in task.get("reminders", []) if not r.get("fired_at")]
        if pending:
            continue
        # Create the next occurrence, then retire the one that just passed so it
        # leaves the active list (its reminders have already fired).
        await engine._spawn_next_occurrence(task, actor="scheduler")  # noqa: SLF001 — same package
        await store.update_task(
            task["id"],
            {"status": "done", "completed_at": now_iso(), "_event": "recurrence_rolled"},
            actor="scheduler",
        )


def _friendly_when(iso: str | None) -> str:
    dt = parse_iso(iso)
    return dt.strftime("%b %d, %H:%M UTC") if dt else ""


def _minus_minutes(iso: str, minutes: int) -> str:
    from datetime import timedelta
    dt = parse_iso(iso)
    return to_iso(dt - timedelta(minutes=minutes)) if dt else iso
