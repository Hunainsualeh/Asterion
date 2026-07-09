"""Tests for the assistant-platform: recurrence, timeutil, the Task Engine,
and the new intent/control routing. Async tests run via conftest's asyncio.run
shim; the task DB is redirected to a temp file so the suite never touches the
real tasks.db."""
from __future__ import annotations

import pytest

from app.control import actions
from app.orchestration.intent import classify_heuristic
from app.tasks import recurrence
from app.tasks.timeutil import local_to_utc_iso, parse_iso, to_iso


@pytest.fixture(autouse=True)
def _temp_task_db(tmp_path, monkeypatch):
    from app.tasks import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "tasks_test.db")
    store.init_sync()
    yield


# --------------------------------------------------------------------------- recurrence
def test_recurrence_daily():
    assert recurrence.next_occurrence("FREQ=DAILY", "2026-07-04T09:00:00Z", "UTC") == "2026-07-05T09:00:00Z"


def test_recurrence_weekly_byday():
    # 2026-07-06 is a Monday; the next weekly Monday is a week later.
    assert recurrence.next_occurrence("FREQ=WEEKLY;BYDAY=MO", "2026-07-06T09:00:00Z", "UTC") == "2026-07-13T09:00:00Z"


def test_recurrence_monthly_clamps_day():
    # Jan 31 + 1 month clamps to Feb 28 (2026 is not a leap year).
    assert recurrence.next_occurrence("FREQ=MONTHLY", "2026-01-31T08:00:00Z", "UTC") == "2026-02-28T08:00:00Z"


def test_recurrence_invalid():
    assert recurrence.next_occurrence("FREQ=NONSENSE", "2026-07-04T09:00:00Z", "UTC") is None
    assert not recurrence.is_valid_rrule("FREQ=NONSENSE")
    assert recurrence.is_valid_rrule("FREQ=WEEKLY;BYDAY=MO,WE")


def test_describe_rrule():
    assert recurrence.describe_rrule("FREQ=DAILY") == "every day"
    assert "Mon" in recurrence.describe_rrule("FREQ=WEEKLY;BYDAY=MO")


# --------------------------------------------------------------------------- timeutil
def test_local_to_utc_karachi():
    # 09:00 in Asia/Karachi (UTC+5) is 04:00 UTC. Requires the tzdata package.
    assert local_to_utc_iso("2026-07-05T09:00", "Asia/Karachi") == "2026-07-05T04:00:00Z"


def test_parse_and_roundtrip():
    dt = parse_iso("2026-07-05T04:00:00Z")
    assert dt is not None
    assert to_iso(dt) == "2026-07-05T04:00:00Z"


# --------------------------------------------------------------------------- engine
async def test_engine_create_arms_reminder():
    from app.tasks import duequeue, engine
    from app.redis.client import get_redis, key

    r = await get_redis()
    await r.delete(key("duequeue"))
    task = await engine.create_task({
        "title": "submit visa documents",
        "due": "2026-07-05T09:00", "timezone": "Asia/Karachi",
        "priority": "high", "tags": ["visa"], "reminders": [{"offset_min": 0}],
    })
    assert task["due_at"] == "2026-07-05T04:00:00Z"   # tz applied
    assert task["priority"] == "high"
    assert task["tags"] == ["visa"]
    assert len(task["reminders"]) == 1
    assert await r.zcard(key("duequeue")) >= 1


async def test_engine_complete_recurring_spawns_next():
    from app.tasks import engine, store

    task = await engine.create_task({
        "title": "german practice", "due": "2026-07-06T09:00", "timezone": "UTC",
        "recurrence": "FREQ=WEEKLY;BYDAY=MO", "reminders": [{"offset_min": 0}],
    })
    await engine.complete_task(task["id"])
    done = await store.get_task(task["id"])
    assert done["status"] == "done"
    # a fresh occurrence for the next Monday should now exist
    active = await store.list_tasks({"user_id": "local", "status": ["open"], "q": "german"})
    assert any(t["due_at"] == "2026-07-13T09:00:00Z" for t in active)


async def test_engine_reschedule_rearms():
    from app.tasks import engine, store

    task = await engine.create_task({"title": "dentist", "due": "2026-07-05T10:00", "timezone": "UTC",
                                     "reminders": [{"offset_min": 0}]})
    updated = await engine.update_task(task["id"], {"due": "2026-07-08T15:00", "timezone": "UTC"})
    assert updated is not None
    assert updated["due_at"] == "2026-07-08T15:00:00Z"


# --------------------------------------------------------------------------- routing
def test_intent_task_and_control_heuristics():
    assert classify_heuristic("remind me tomorrow at 9am to submit my visa documents").kind == "task_command"
    assert classify_heuristic("delete my gym reminder").kind == "task_command"
    assert classify_heuristic("show my upcoming tasks").kind == "task_command"
    assert classify_heuristic("open settings").kind == "system_control"
    assert classify_heuristic("delete this chat").kind == "system_control"
    # a build request must NOT be captured by the task heuristic
    assert classify_heuristic("build a task manager app") is None


def test_control_resolve_flags():
    delete = actions.resolve("delete this chat")
    assert delete is not None and delete.action == "delete_chat"
    assert delete.destructive and delete.confirm
    theme = actions.resolve("switch to dark mode")
    assert theme is not None and theme.action == "switch_theme"
    assert theme.params.get("theme") == "dark"
    assert not theme.destructive
