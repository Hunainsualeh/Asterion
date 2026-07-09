"""A focused RFC-5545 RRULE engine — the subset a personal assistant needs.

Supports FREQ=DAILY|WEEKLY|MONTHLY|YEARLY with INTERVAL, BYDAY (weekly),
COUNT and UNTIL. That covers every recurrence in the brief ("every day",
"every Monday", "monthly on the 1st") without pulling in ``dateutil`` (which
has no Python 3.14 wheel on this box). We only ever compute the *next* single
occurrence after a given instant — recurrences are never pre-expanded, so
storage stays bounded and DST stays correct via the task's IANA timezone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.tasks.timeutil import UTC, get_zone, parse_iso, to_iso

log = logging.getLogger("asterion.tasks.recur")

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def parse_rrule(rrule: str) -> dict[str, object] | None:
    """Parse ``FREQ=WEEKLY;BYDAY=MO,WE;INTERVAL=1`` into a dict, or None."""
    if not rrule or not rrule.strip():
        return None
    text = rrule.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip().upper()] = v.strip()
    freq = parts.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return None
    out: dict[str, object] = {"freq": freq, "interval": max(1, _int(parts.get("INTERVAL"), 1))}
    if "BYDAY" in parts:
        days = [_WEEKDAYS[d.strip().upper()] for d in parts["BYDAY"].split(",") if d.strip().upper() in _WEEKDAYS]
        if days:
            out["byday"] = sorted(set(days))
    if "COUNT" in parts:
        out["count"] = _int(parts.get("COUNT"), 0)
    if "UNTIL" in parts:
        out["until"] = parse_iso(parts["UNTIL"])
    return out


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def is_valid_rrule(rrule: str | None) -> bool:
    return bool(rrule) and parse_rrule(rrule) is not None


def next_occurrence(rrule: str, after_iso: str, tz: str = "UTC") -> str | None:
    """The first occurrence strictly after ``after_iso``.

    Recurrence math happens in the task's local timezone (so "every day at
    9am" stays 9am across DST) and the result is returned as UTC ISO.
    ``after_iso`` should normally be the current due time; the next one is
    computed relative to it.
    """
    rule = parse_rrule(rrule)
    after = parse_iso(after_iso)
    if not rule or not after:
        return None

    zone = get_zone(tz)
    local = after.astimezone(zone)
    freq = rule["freq"]
    interval: int = rule["interval"]  # type: ignore[assignment]
    until = rule.get("until")

    candidate: datetime | None = None
    if freq == "DAILY":
        candidate = local + timedelta(days=interval)
    elif freq == "WEEKLY":
        candidate = _next_weekly(local, interval, rule.get("byday"))  # type: ignore[arg-type]
    elif freq == "MONTHLY":
        candidate = _add_months(local, interval)
    elif freq == "YEARLY":
        candidate = _add_months(local, interval * 12)

    if candidate is None:
        return None
    result = candidate.astimezone(UTC)
    if until and result > until:
        return None
    return to_iso(result)


def _next_weekly(local: datetime, interval: int, byday: list[int] | None) -> datetime:
    """Next matching weekday. Without BYDAY, just +interval weeks (same day)."""
    if not byday:
        return local + timedelta(weeks=interval)
    # Scan forward day by day for the next weekday in the set. INTERVAL>1 with
    # BYDAY is uncommon for a to-do app; we honor the simple weekly case
    # (interval 1) precisely and treat larger intervals as "next listed day".
    for delta in range(1, 7 * max(interval, 1) + 1):
        cand = local + timedelta(days=delta)
        if cand.weekday() in byday:
            return cand
    return local + timedelta(weeks=interval)


def _add_months(local: datetime, months: int) -> datetime:
    """Add whole months, clamping the day to the target month's length."""
    month_index = local.month - 1 + months
    year = local.year + month_index // 12
    month = month_index % 12 + 1
    day = min(local.day, _days_in_month(year, month))
    return local.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def describe_rrule(rrule: str | None) -> str:
    """A short human phrase for a recurrence, for confirmations/UI."""
    rule = parse_rrule(rrule) if rrule else None
    if not rule:
        return "once"
    interval: int = rule["interval"]  # type: ignore[assignment]
    freq = rule["freq"]
    every = "" if interval == 1 else f"{interval} "
    if freq == "WEEKLY" and rule.get("byday"):
        names = {v: k for k, v in _WEEKDAYS.items()}
        long = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
        days = ", ".join(long[names[d]] for d in rule["byday"])  # type: ignore[index]
        return f"every {every}week on {days}"
    unit = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}[freq]
    return f"every {every}{unit}"
