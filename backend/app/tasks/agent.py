"""The Task Agent — conversational task management.

Independent of the chat pipeline's conversation memory: it takes one message,
extracts a structured task command (resolving "tomorrow at 9am" against the
user's current time and timezone), dispatches it to the Task Engine, and
replies with a confirmation. Missing essential info is asked back via the same
clarify mechanism the build lanes use, so "remind me tomorrow" → "what should
I remind you about?" costs one round-trip, not a form.

It publishes onto the project's normal event stream (so the confirmation shows
in chat) plus a `ui_action` event telling the client to refresh its task views.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app.llm.client import chat_completion
from app.orchestration.events import publish_event
from app.services import project_store as store_proj
from app.tasks import engine, store
from app.tasks.recurrence import describe_rrule
from app.tasks.timeutil import get_zone, now_utc, parse_iso

log = logging.getLogger("asterion.tasks.agent")

USER = "local"

_EXTRACT_PROMPT = """You are the Task Agent for a personal assistant. Turn the user's message into ONE structured task command. Resolve every relative time ("tomorrow", "next Monday", "in 2 hours", "tonight") into an ABSOLUTE local date-time using the reference clock below. Output local wall-clock time — do NOT convert to UTC.

Reference now: {now_local} ({weekday}), timezone {tz}.

Return ONLY JSON:
{{"op": "create|reschedule|update|complete|cancel|delete|list|none",
  "title": "<for create: what to do; for reschedule/complete/cancel/delete/update: words to FIND the existing task>",
  "description": "",
  "due": "<YYYY-MM-DDTHH:MM local, or YYYY-MM-DD for an all-day date, or empty>",
  "all_day": false,
  "recurrence": "<RFC5545 RRULE like FREQ=WEEKLY;BYDAY=MO, or empty>",
  "priority": "<low|normal|high|urgent or empty>",
  "tags": ["..."],
  "window": "<for list: today|week|overdue|all, else empty>",
  "clarify": "<a question if you truly cannot proceed, else empty>"}}

Rules:
- "remind me / add a task / schedule" -> op create. If no time is given, leave due empty (a someday to-do is fine) — do NOT ask for a time.
- "every day/Monday/week/month" -> set recurrence and a due for the FIRST occurrence.
- "reschedule/move X to <time>" -> op reschedule, title=words identifying X, due=new time.
- "mark X done / finished / completed" -> op complete. "cancel/remove/delete X" -> op cancel or delete.
- "show/list my tasks", "what's due" -> op list with the right window.
- Only set clarify when op is create and there is NO idea what the task even is. Never ask about priority/tags/optional details — pick sensible defaults.

Examples:
"Remind me tomorrow at 9am to submit my visa documents" -> {{"op":"create","title":"submit visa documents","due":"{tomorrow}T09:00","recurrence":"","priority":"","tags":[],"clarify":""}}
"Schedule a reminder every Monday for German practice" -> {{"op":"create","title":"German practice","due":"{next_monday}T09:00","recurrence":"FREQ=WEEKLY;BYDAY=MO","priority":"","tags":[],"clarify":""}}
"Reschedule my blocked account reminder to Friday" -> {{"op":"reschedule","title":"blocked account","due":"{friday}T09:00","clarify":""}}
"Delete my gym reminder" -> {{"op":"delete","title":"gym","clarify":""}}
"Show my upcoming tasks" -> {{"op":"list","window":"week","clarify":""}}
"""


async def run(pid: str, message: str, tz: str = "UTC", context: str = "") -> None:
    """Handle a task command end-to-end and publish the outcome to chat."""
    await store_proj.set_running(pid, True)
    await store_proj.set_status(pid, "running")
    try:
        cmd = await _extract(message, tz)
        reply, changed = await _dispatch(pid, cmd, tz)
    except Exception as exc:  # noqa: BLE001 — a task command must never crash the chat
        log.exception("task agent failed for %s", pid)
        reply, changed = (
            "I couldn't process that task request — could you rephrase it? "
            f"(reason: {exc.__class__.__name__})",
            False,
        )
    finally:
        await store_proj.set_running(pid, False)

    await store_proj.set_status(pid, "complete")
    await store_proj.append_history(pid, "assistant", reply)
    await publish_event(pid, "result", "task_agent", "Task update", {"result": reply, "lane": "task"})
    if changed:
        await publish_event(pid, "ui_action", "task_agent", "Refresh tasks",
                            {"action": "refresh_tasks", "destructive": False})
    await publish_event(pid, "done", "system", "Task handled", {"lane": "task"})


async def _extract(message: str, tz: str) -> dict:
    now = now_utc().astimezone(get_zone(tz))
    from datetime import timedelta

    def _d(offset_days: int) -> str:
        return (now + timedelta(days=offset_days)).strftime("%Y-%m-%d")

    # next Monday (strictly after today)
    days_to_mon = (7 - now.weekday()) % 7 or 7
    days_to_fri = (4 - now.weekday()) % 7 or 7
    prompt = _EXTRACT_PROMPT.format(
        now_local=now.strftime("%Y-%m-%d %H:%M"),
        weekday=now.strftime("%A"),
        tz=tz,
        tomorrow=_d(1),
        next_monday=(now + timedelta(days=days_to_mon)).strftime("%Y-%m-%d"),
        friday=(now + timedelta(days=days_to_fri)).strftime("%Y-%m-%d"),
    )
    resp = await chat_completion(
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": message[:1000]}],
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


async def _dispatch(pid: str, cmd: dict, tz: str) -> tuple[str, bool]:
    op = (cmd.get("op") or "none").lower()
    clarify = (cmd.get("clarify") or "").strip()
    if clarify and op in ("create", "none"):
        return f"Quick question — {clarify}", False

    if op == "create":
        return await _create(pid, cmd, tz), True
    if op in ("reschedule", "update"):
        return await _modify(cmd, tz, op), True
    if op in ("complete", "cancel", "delete"):
        return await _lifecycle(cmd, op)
    if op == "list":
        return await _list(cmd), False
    return (
        "I can set reminders and manage your tasks — try “remind me tomorrow at 9am to…” "
        "or “show my tasks”.",
        False,
    )


async def _create(pid: str, cmd: dict, tz: str) -> str:
    title = (cmd.get("title") or "").strip()
    if not title:
        return "What should I remind you about?"
    due = (cmd.get("due") or "").strip() or None
    task = await engine.create_task({
        "title": title,
        "description": cmd.get("description", ""),
        "due": due,
        "due_has_time": (not cmd.get("all_day")) if due else None,
        "timezone": tz,
        "priority": cmd.get("priority") or "normal",
        "recurrence": cmd.get("recurrence") or None,
        "tags": cmd.get("tags") or [],
        "chat_id": pid,
        "source": "chat",
        "user_id": USER,
        "actor": "task_agent",
    })
    return _confirm_created(task)


async def _modify(cmd: dict, tz: str, op: str) -> str:
    match = await _find(cmd.get("title", ""))
    if isinstance(match, str):
        return match
    fields: dict = {}
    if cmd.get("due"):
        fields["due"] = cmd["due"]
        fields["timezone"] = tz
    if cmd.get("recurrence"):
        fields["recurrence"] = cmd["recurrence"]
    if cmd.get("priority"):
        fields["priority"] = cmd["priority"]
    if cmd.get("description"):
        fields["description"] = cmd["description"]
    if not fields:
        return f"What would you like to change about “{match['title']}”?"
    updated = await engine.update_task(match["id"], fields, actor="task_agent")
    if not updated:
        return "I couldn't update that task — it may have been removed."
    when = _when_phrase(updated)
    return f"✓ Updated “{updated['title']}”{(' — now ' + when) if when else ''}."


async def _lifecycle(cmd: dict, op: str) -> tuple[str, bool]:
    match = await _find(cmd.get("title", ""))
    if isinstance(match, str):
        return match, False
    if op == "complete":
        await engine.complete_task(match["id"], actor="task_agent")
        return f"✓ Marked “{match['title']}” as done.", True
    if op == "cancel":
        await engine.cancel_task(match["id"], actor="task_agent")
        return f"✓ Cancelled “{match['title']}”.", True
    await engine.delete_task(match["id"])
    return f"✓ Deleted “{match['title']}”.", True


async def _list(cmd: dict) -> str:
    window = (cmd.get("window") or "all").lower()
    filters: dict = {"user_id": USER, "status": ["open", "in_progress"]}
    from app.tasks.timeutil import now_iso, to_iso
    from datetime import timedelta

    if window == "today":
        end = now_utc().replace(hour=23, minute=59, second=59)
        filters["due_to"] = to_iso(end)
    elif window == "week":
        filters["due_to"] = to_iso(now_utc() + timedelta(days=7))
    elif window == "overdue":
        filters["due_to"] = now_iso()

    tasks = await store.list_tasks(filters)
    if not tasks:
        return "You have no tasks in that window. 🎉"
    lines = [f"Here are your tasks ({len(tasks)}):", ""]
    for t in tasks:
        lines.append(f"- {_task_line(t)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _find(text: str) -> dict | str:
    """Locate one active task matching `text`. Returns the task, or a message
    (not found / ambiguous) to relay to the user."""
    text = (text or "").strip()
    if not text:
        return "Which task did you mean?"
    candidates = await store.list_tasks({"user_id": USER, "status": ["open", "in_progress"], "q": text})
    if not candidates:
        # broaden: fetch all active and fuzzy-contains on words
        allt = await store.list_tasks({"user_id": USER, "status": ["open", "in_progress"]})
        words = [w for w in text.lower().split() if len(w) > 2]
        candidates = [t for t in allt if any(w in t["title"].lower() for w in words)]
    if not candidates:
        return f"I couldn't find an active task matching “{text}”."
    if len(candidates) == 1:
        return candidates[0]
    # exact-ish title match wins
    exact = [t for t in candidates if text.lower() in t["title"].lower()]
    if len(exact) == 1:
        return exact[0]
    titles = ", ".join(f"“{t['title']}”" for t in candidates[:5])
    return f"I found a few that match: {titles}. Which one?"


def _confirm_created(task: dict) -> str:
    bits = [f"✓ Got it — I'll remind you about “{task['title']}”"]
    when = _when_phrase(task)
    if when:
        bits.append(f" {when}")
    if task.get("recurrence"):
        bits.append(f", {describe_rrule(task['recurrence'])}")
    if not task.get("due_at"):
        bits[0] = f"✓ Added “{task['title']}” to your tasks"
    return "".join(bits) + "."


def _when_phrase(task: dict) -> str:
    due = parse_iso(task.get("due_at"))
    if not due:
        return ""
    local = due.astimezone(get_zone(task.get("timezone", "UTC")))
    if task.get("due_has_time"):
        return f"on {local.strftime('%a %b %d')} at {local.strftime('%H:%M')}"
    return f"on {local.strftime('%a %b %d')}"


def _task_line(t: dict) -> str:
    when = _when_phrase(t)
    prio = "" if t.get("priority") in ("normal", None, "") else f" [{t['priority']}]"
    rec = f" ({describe_rrule(t['recurrence'])})" if t.get("recurrence") else ""
    return f"**{t['title']}**{prio}{(' — ' + when) if when else ''}{rec}"
