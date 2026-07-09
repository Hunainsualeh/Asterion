"""Action registry, resolver, and audit trail — the enforcement point.

Mirrors the per-agent tool allowlist philosophy already in the codebase
(app/tools/registry.py): every system-control action is declared once with a
permission scope and destructive/confirm flags, and the resolver can only ever
return an action that is in this registry. Unknown or low-confidence phrasing
returns None — the caller then treats the message as ordinary chat.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.redis.client import get_redis, key

log = logging.getLogger("asterion.control")


@dataclass(frozen=True)
class Action:
    name: str
    destructive: bool = False
    confirm: bool = False
    scope: str = "ui"          # ui | nav | chat.write | chat.delete | task.delete
    label: str = ""            # human phrase for the confirmation/log


# The complete surface. Adding a capability = one line here (+ a client handler).
ACTIONS: dict[str, Action] = {
    "navigate":          Action("navigate", scope="nav", label="Open a section"),
    "open_settings":     Action("open_settings", scope="nav", label="Open settings"),
    "open_tasks":        Action("open_tasks", scope="nav", label="Open tasks"),
    "show_notifications":Action("show_notifications", scope="nav", label="Show notifications"),
    "open_profile":      Action("open_profile", scope="nav", label="Open profile"),
    "create_chat":       Action("create_chat", scope="chat.write", label="Start a new chat"),
    "delete_chat":       Action("delete_chat", destructive=True, confirm=True, scope="chat.delete",
                                label="Delete this chat"),
    "delete_all_chats":  Action("delete_all_chats", destructive=True, confirm=True, scope="chat.delete",
                                label="Delete all chats"),
    "switch_theme":      Action("switch_theme", scope="ui", label="Switch theme"),
    "toggle_sidebar":    Action("toggle_sidebar", scope="ui", label="Toggle the sidebar"),
}


@dataclass
class ResolvedAction:
    action: str
    target: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    destructive: bool = False
    confirm: bool = False
    label: str = ""
    audit_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "destructive": self.destructive,
            "confirm": self.confirm,
            "label": self.label,
            "audit_id": self.audit_id,
        }


# ---------------------------------------------------------------------------
# resolution — regex first (fast, precise), LLM fallback for the rest
# ---------------------------------------------------------------------------
_RULES: list[tuple[re.Pattern[str], str, dict[str, Any]]] = [
    (re.compile(r"\b(dark mode|dark theme)\b", re.I), "switch_theme", {"theme": "dark"}),
    (re.compile(r"\b(light mode|light theme)\b", re.I), "switch_theme", {"theme": "light"}),
    (re.compile(r"\b(switch|change|toggle|flip)\b.{0,16}\btheme\b", re.I), "switch_theme", {"theme": "toggle"}),
    (re.compile(r"\b(collapse|hide|toggle|expand|open|show)\b.{0,12}\bsidebar\b", re.I), "toggle_sidebar", {}),
    (re.compile(r"\bnotification", re.I), "show_notifications", {}),
    (re.compile(r"\b(profile|my account)\b", re.I), "open_profile", {}),
    (re.compile(r"\bsettings?\b|\bpreferences\b", re.I), "open_settings", {}),
    (re.compile(r"\b(tasks?|reminders?|to-?dos?)\b", re.I), "open_tasks", {}),
    # Bulk delete must be checked BEFORE the single-chat rule so "delete all
    # projects" doesn't get read as "delete (this) project".
    (re.compile(r"\b(delete|remove|clear|wipe|discard|trash)\b.{0,20}\b(all|every|my)\b.{0,16}\b(chats?|conversations?|projects?)\b", re.I),
     "delete_all_chats", {}),
    (re.compile(r"\b(delete|remove|discard|trash)\b.{0,20}\b(this|current|the)\b.{0,6}\b(chat|conversation|project)s?\b", re.I),
     "delete_chat", {}),
    (re.compile(r"\b(new|create|start|another)\b.{0,12}\b(chat|conversation|project)\b", re.I), "create_chat", {}),
]


def resolve(text: str) -> ResolvedAction | None:
    """Map a control message to a whitelisted action, or None if unclear."""
    stripped = text.strip()
    for pattern, action_name, params in _RULES:
        if pattern.search(stripped):
            return _build(action_name, params)
    return None


async def resolve_smart(text: str) -> ResolvedAction | None:
    """Regex first; fall back to a cheap LLM classification for phrasing the
    rules miss. Still constrained to the registry — the LLM only picks a name."""
    direct = resolve(text)
    if direct is not None:
        return direct
    try:
        from app.config import get_settings
        from app.llm.client import chat_completion

        names = ", ".join(ACTIONS.keys())
        resp = await chat_completion(
            messages=[
                {"role": "system", "content": (
                    "Map the user's app-control request to exactly one action name from this list, "
                    f"or \"none\" if none fit: {names}. For switch_theme include a theme param "
                    "(dark|light|toggle). Return ONLY JSON: "
                    '{"action":"<name|none>","params":{}}'
                )},
                {"role": "user", "content": text[:300]},
            ],
            model=get_settings().groq_fast_model,
            temperature=0.0,
            max_tokens=80,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        name = data.get("action")
        if name in ACTIONS:
            return _build(name, data.get("params") if isinstance(data.get("params"), dict) else {})
    except Exception as exc:  # noqa: BLE001 — resolution failure = treat as plain chat
        log.warning("smart action resolve failed: %s", exc)
    return None


def _build(action_name: str, params: dict[str, Any]) -> ResolvedAction:
    spec = ACTIONS[action_name]
    target = ""
    if action_name == "open_settings":
        target = "settings"
    elif action_name == "open_tasks":
        target = "settings"  # tasks live in a Settings tab
        params = {**params, "tab": "tasks"}
    elif action_name == "navigate":
        target = str(params.get("target", ""))
    return ResolvedAction(
        action=spec.name,
        target=target,
        params=params or {},
        destructive=spec.destructive,   # server-authoritative, never from the client
        confirm=spec.confirm,
        label=spec.label,
    )


# ---------------------------------------------------------------------------
# audit trail — two-phase: requested → executed | confirmed | cancelled
# ---------------------------------------------------------------------------
def _audit_key(user: str) -> str:
    return key("audit", user)


async def audit(user: str, phase: str, action: str, detail: dict[str, Any] | None = None,
                audit_id: str | None = None) -> str:
    r = await get_redis()
    aid = audit_id or uuid.uuid4().hex[:12]
    entry = {
        "audit_id": aid,
        "phase": phase,           # requested | executed | confirmed | cancelled | denied
        "action": action,
        "detail": detail or {},
        "ts": time.time(),
    }
    await r.xadd(_audit_key(user), {"e": json.dumps(entry)}, maxlen=1000, approximate=True)
    return aid


async def audit_log(user: str, limit: int = 100) -> list[dict[str, Any]]:
    r = await get_redis()
    raw = await r.xrevrange(_audit_key(user), count=limit)
    out: list[dict[str, Any]] = []
    for entry_id, fields in raw:
        payload = fields.get(b"e") or fields.get("e")
        if isinstance(payload, bytes):
            payload = payload.decode()
        try:
            ev = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        ev["id"] = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        out.append(ev)
    return out
