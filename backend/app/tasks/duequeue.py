"""Redis ZSET hot-index of reminders that are due.

member = reminder_id, score = fire_at (epoch seconds). This is a rebuildable
cache over the reminders table — never the source of truth. On boot the
scheduler calls rehydrate() to repopulate it from SQLite, so a server restart
(the documented uvicorn --reload gotcha) never loses a pending reminder.
"""
from __future__ import annotations

import logging

from app.redis.client import get_redis, key
from app.tasks import store
from app.tasks.timeutil import to_epoch

log = logging.getLogger("asterion.tasks.duequeue")


def _qkey() -> str:
    return key("duequeue")


async def arm(reminder_id: str, fire_at_iso: str) -> None:
    epoch = to_epoch(fire_at_iso)
    if epoch is None:
        return
    r = await get_redis()
    await r.zadd(_qkey(), {reminder_id: epoch})


async def arm_many(reminders: list[dict]) -> None:
    mapping = {}
    for rem in reminders:
        epoch = to_epoch(rem.get("fire_at"))
        if epoch is not None:
            mapping[rem["id"]] = epoch
    if mapping:
        r = await get_redis()
        await r.zadd(_qkey(), mapping)


async def disarm(*reminder_ids: str) -> None:
    ids = [rid for rid in reminder_ids if rid]
    if not ids:
        return
    r = await get_redis()
    await r.zrem(_qkey(), *ids)


async def pop_due(now_epoch: float, limit: int = 100) -> list[str]:
    """Reminder ids whose fire time has arrived; removed from the index."""
    r = await get_redis()
    raw = await r.zrangebyscore(_qkey(), 0, now_epoch, start=0, num=limit)
    ids = [(m.decode() if isinstance(m, bytes) else m) for m in raw]
    if ids:
        await r.zrem(_qkey(), *ids)
    return ids


async def rehydrate() -> int:
    """Rebuild the whole index from the reminders table. Returns count armed."""
    r = await get_redis()
    await r.delete(_qkey())
    pending = await store.pending_reminders()
    await arm_many(pending)
    log.info("due-queue rehydrated: %d pending reminder(s)", len(pending))
    return len(pending)
