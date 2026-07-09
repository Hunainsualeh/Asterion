"""Timezone-aware datetime helpers, stdlib-only (zoneinfo on Python 3.9+).

The whole subsystem stores absolute instants as ISO-8601 UTC strings (with a
trailing ``Z``) so they sort lexicographically and are unambiguous in SQLite.
Display/recurrence use the task's own IANA timezone. Nothing here depends on
``dateutil`` — Python 3.14 ships ``zoneinfo`` in the stdlib.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("asterion.tasks.time")

UTC = timezone.utc


def get_zone(tz: str | None):
    """Resolve an IANA tz name to a tzinfo; fall back to UTC on anything odd."""
    if not tz or tz.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("unknown timezone %r; using UTC", tz)
        return UTC


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return to_iso(now_utc())


def to_iso(dt: datetime) -> str:
    """A UTC ISO-8601 string ending in ``Z`` (seconds precision)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str | None) -> datetime | None:
    """Parse any ISO-8601 string into an aware UTC datetime, or None."""
    if not s:
        return None
    txt = s.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        # Accept a bare date (all-day task).
        try:
            dt = datetime.strptime(txt[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_epoch(s: str | None) -> float | None:
    dt = parse_iso(s)
    return dt.timestamp() if dt else None


def local_to_utc_iso(naive_or_aware: str, tz: str) -> str | None:
    """Interpret a wall-clock ISO string as being in ``tz`` and return UTC ISO.

    Used when the LLM extracts a local time like ``2026-07-05T09:00`` for a
    user in ``Asia/Karachi`` — that is 04:00 UTC, and everything downstream
    (the due-queue, comparisons) works in UTC.
    """
    txt = naive_or_aware.strip()
    if txt.endswith("Z") or "+" in txt[10:]:
        # already carries an offset — trust it
        return to_iso(parse_iso(txt)) if parse_iso(txt) else None
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.strptime(txt[:10], "%Y-%m-%d")
        except ValueError:
            return None
    dt = dt.replace(tzinfo=get_zone(tz))
    return to_iso(dt)
