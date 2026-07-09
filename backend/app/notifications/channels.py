"""Notification channel dispatcher — the seam for future integrations.

A reminder's `channel` column decides how it reaches the user. Today "inapp"
and "browser" both resolve to the in-app feed (browser delivery is finished on
the client, which raises a native Notification from the SSE frame). Email,
calendar, and WhatsApp are registered as stubs so a task can already carry that
channel without breaking — wiring a real provider is one handler swap here, no
schema change (see deliverable #10, "External integrations").
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.notifications import service as notifications

log = logging.getLogger("asterion.notifications.channels")

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]

_CHANNELS: dict[str, Handler] = {}


def register(name: str) -> Callable[[Handler], Handler]:
    def _wrap(fn: Handler) -> Handler:
        _CHANNELS[name] = fn
        return fn

    return _wrap


async def deliver(channel: str, user: str, ctx: dict[str, Any]) -> None:
    """Route a reminder to its channel; unknown channels degrade to in-app."""
    handler = _CHANNELS.get(channel) or _CHANNELS["inapp"]
    try:
        await handler(user, ctx)
    except Exception:  # noqa: BLE001 — a channel failure must never break the tick
        log.exception("channel %s failed; falling back to in-app", channel)
        if channel != "inapp":
            await _CHANNELS["inapp"](user, ctx)


@register("inapp")
async def _inapp(user: str, ctx: dict[str, Any]) -> None:
    await notifications.notify(
        user,
        kind=ctx.get("kind", "reminder"),
        title=ctx["title"],
        body=ctx.get("body", ""),
        task_id=ctx.get("task_id"),
        action=ctx.get("action"),
        tone=ctx.get("tone", "info"),
    )


# The browser channel is delivered by the client (it raises a native
# Notification from the same SSE frame), so on the server it's identical to
# in-app: publish once, the client decides whether to also pop an OS toast.
@register("browser")
async def _browser(user: str, ctx: dict[str, Any]) -> None:
    await _inapp(user, ctx)


# ---- future integrations (stubs; log intent, then still surface in-app) ----
@register("email")
async def _email(user: str, ctx: dict[str, Any]) -> None:
    log.info("[email channel not configured] would email %s: %s", user, ctx.get("title"))
    await _inapp(user, ctx)


@register("whatsapp")
async def _whatsapp(user: str, ctx: dict[str, Any]) -> None:
    log.info("[whatsapp channel not configured] would message %s: %s", user, ctx.get("title"))
    await _inapp(user, ctx)


@register("calendar")
async def _calendar(user: str, ctx: dict[str, Any]) -> None:
    log.info("[calendar channel not configured] would add event for %s: %s", user, ctx.get("title"))
    await _inapp(user, ctx)


def available_channels() -> list[str]:
    return list(_CHANNELS.keys())
