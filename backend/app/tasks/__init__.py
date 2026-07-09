"""Task-management subsystem — the assistant-platform's durable side.

Everything here runs independently of the chat pipeline:

  store.py       SQLite data-access (tasks.db) — the source of truth
  timeutil.py    timezone-aware datetime helpers (stdlib zoneinfo)
  recurrence.py  RFC-5545 RRULE subset → next occurrence, no external deps
  duequeue.py    Redis ZSET hot-index of reminders that are due
  engine.py      Task Management Engine: validation, lifecycle, reminders
  scheduler.py   background loop that fires reminders / sweeps missed / recurs
  agent.py       the Task Agent: NL → task ops via the tool registry

Tasks reference an *optional* originating chat_id and survive its deletion,
so the task system keeps working even when the conversation that created a
task is gone.
"""
