"""Runs a system-control command: resolve → audit → emit ui_action → confirm.

The mutation itself happens on the client (navigation) or via the existing
REST endpoints (delete chat), never here — this layer only *triggers* it and
records the audit trail. Destructive actions carry confirm=True so the client
shows a dialog before doing anything irreversible.
"""
from __future__ import annotations

import logging

from app.control import actions
from app.orchestration.events import publish_event
from app.services import project_store as store

log = logging.getLogger("asterion.control.service")

USER = "local"

_NARRATION = {
    "open_settings": "Opening settings.",
    "open_tasks": "Opening your tasks.",
    "show_notifications": "Here are your notifications.",
    "open_profile": "Opening your profile.",
    "create_chat": "Starting a new chat.",
    "switch_theme": "Switching the theme.",
    "toggle_sidebar": "Toggling the sidebar.",
    "navigate": "Taking you there.",
}


async def run(pid: str, message: str) -> None:
    """Handle a system-control message and publish the resulting ui_action."""
    await store.set_running(pid, True)
    await store.set_status(pid, "running")
    try:
        resolved = await actions.resolve_smart(message)
    finally:
        await store.set_running(pid, False)
    await store.set_status(pid, "complete")

    if resolved is None:
        text = ("I can open Settings, Tasks, Notifications or your Profile, start or delete a chat, "
                "and switch the theme — which would you like?")
        await store.append_history(pid, "assistant", text)
        await publish_event(pid, "result", "control", "Not sure", {"result": text, "lane": "task"})
        await publish_event(pid, "done", "system", "Handled", {"lane": "task"})
        return

    if resolved.action == "delete_chat":
        resolved.target = pid  # "this chat" = the one the command was typed in

    resolved.audit_id = await actions.audit(
        USER, "requested", resolved.action,
        {"target": resolved.target, "params": resolved.params, "pid": pid},
    )

    reply = _NARRATION.get(resolved.action, "Done.")
    if resolved.confirm:
        reply = f"{resolved.label}? I'll ask you to confirm first."

    await store.append_history(pid, "assistant", reply)
    # The client's ActionExecutor performs the action (with a confirm dialog if
    # needed) the moment it sees this event.
    await publish_event(pid, "ui_action", "control", resolved.label or resolved.action, resolved.as_payload())
    await publish_event(pid, "result", "control", "Control", {"result": reply, "lane": "task"})
    await publish_event(pid, "done", "system", "Handled", {"lane": "task"})
