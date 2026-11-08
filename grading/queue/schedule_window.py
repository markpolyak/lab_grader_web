"""Schedule lookup for the daily defense queue."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from grading.schedule import (
    ScheduleConfigError,
    ScheduledSession,
    parse_course_schedule,
    schedule_path_for_course,
)


_CACHE_LOCK = threading.Lock()
_SOURCE_CACHE: dict[tuple[str, str, int], object] = {}
_SESSION_CACHE: dict[tuple[str, str, date, int, int], list[ScheduledSession]] = {}
_NEXT_SESSION_DAYS = 8


@dataclass(frozen=True)
class ScheduleContext:
    source: object
    timezone: timezone | ZoneInfo
    now: datetime


def _timezone_for(value: str) -> timezone | ZoneInfo:
    fixed_offset = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", value)
    if fixed_offset:
        sign = 1 if fixed_offset.group(1) == "+" else -1
        hours = int(fixed_offset.group(2))
        minutes = int(fixed_offset.group(3) or "0")
        if hours > 23 or minutes > 59:
            raise ScheduleConfigError(f"Invalid schedule timezone: {value!r}")
        return timezone(sign * timedelta(hours=hours, minutes=minutes))

    try:
        return ZoneInfo(value)
    except (KeyError, ValueError) as error:
        raise ScheduleConfigError(f"Unknown schedule timezone: {value!r}") from error


def _load_source(
    course_id: str,
    group_id: str,
    schedules_dir: str | Path,
):
    path = schedule_path_for_course(course_id, schedules_dir)
    if not path.is_file():
        return None, None

    modified_ns = path.stat().st_mtime_ns
    cache_key = (course_id, group_id, modified_ns)
    with _CACHE_LOCK:
        source = _SOURCE_CACHE.get(cache_key)
    if source is not None:
        return source, modified_ns

    sources = parse_course_schedule(course_id, schedules_dir)
    source = sources.get(str(group_id))
    if source is None:
        return None, modified_ns

    # Do not publish a parsed source under an mtime that changed while reading.
    if path.stat().st_mtime_ns != modified_ns:
        return _load_source(course_id, group_id, schedules_dir)

    with _CACHE_LOCK:
        stale_keys = [
            key for key in _SOURCE_CACHE
            if key[:2] == (course_id, group_id) and key != cache_key
        ]
        for key in stale_keys:
            _SOURCE_CACHE.pop(key, None)
        _SOURCE_CACHE[cache_key] = source
    return source, modified_ns


def _context_for_now(source, now: datetime | None) -> ScheduleContext:
    local_timezone = _timezone_for(source.timezone or "Europe/Moscow")
    if now is None:
        local_now = datetime.now(local_timezone).replace(tzinfo=None)
    elif now.tzinfo is None:
        local_now = now
    else:
        local_now = now.astimezone(local_timezone).replace(tzinfo=None)
    return ScheduleContext(source, local_timezone, local_now)


def _sessions_from(
    course_id: str,
    group_id: str,
    source,
    modified_ns: int,
    start_date: date,
    day_count: int,
) -> list[ScheduledSession]:
    cache_key = (course_id, group_id, start_date, day_count, modified_ns)
    with _CACHE_LOCK:
        cached = _SESSION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    since = datetime.combine(start_date, time.min)
    until = since + timedelta(days=day_count)
    sessions = list(source.list_sessions(group_id, since, until))

    with _CACHE_LOCK:
        stale_keys = [
            key for key in _SESSION_CACHE
            if key[:2] == (course_id, group_id)
            and (key[2] != start_date or key[4] != modified_ns)
        ]
        for key in stale_keys:
            _SESSION_CACHE.pop(key, None)
        _SESSION_CACHE[cache_key] = sessions
    return sessions


def sessions_for_date(
    course_id: str,
    group_id: str,
    session_date: date,
    schedules_dir: str | Path = "schedules",
) -> list[ScheduledSession]:
    """Return sessions after resolving weekly slots and date corrections."""
    source, modified_ns = _load_source(course_id, group_id, schedules_dir)
    if source is None:
        return []
    return _sessions_from(
        course_id, group_id, source, modified_ns, session_date, day_count=1
    )


def active_session_for(
    course_id: str,
    group_id: str,
    lab_key: str,
    now: datetime | None = None,
    schedules_dir: str | Path = "schedules",
) -> ScheduledSession | None:
    """Find an open session allowing this lab on the group's local date."""
    source, modified_ns = _load_source(course_id, group_id, schedules_dir)
    if source is None:
        return None

    context = _context_for_now(source, now)
    sessions = _sessions_from(
        course_id,
        group_id,
        source,
        modified_ns,
        context.now.date(),
        day_count=1,
    )
    active = [
        session
        for session in sessions
        if session.auto_open_at <= context.now <= session.auto_close_at
        and (
            session.allowed_lab_ids is None
            or lab_key in session.allowed_lab_ids
        )
    ]
    return min(active, key=lambda session: session.starts_at) if active else None


def next_session_for(
    course_id: str,
    group_id: str,
    lab_key: str,
    now: datetime | None = None,
    schedules_dir: str | Path = "schedules",
) -> ScheduledSession | None:
    """Find the next future session allowing this lab within eight days."""
    source, modified_ns = _load_source(course_id, group_id, schedules_dir)
    if source is None:
        return None

    context = _context_for_now(source, now)
    sessions = _sessions_from(
        course_id,
        group_id,
        source,
        modified_ns,
        context.now.date(),
        day_count=_NEXT_SESSION_DAYS,
    )
    upcoming = [
        session
        for session in sessions
        if session.starts_at > context.now
        and (
            session.allowed_lab_ids is None
            or lab_key in session.allowed_lab_ids
        )
    ]
    return min(upcoming, key=lambda session: session.starts_at) if upcoming else None


def reset_schedule_cache() -> None:
    """Clear schedule caches for tests and explicit configuration reloads."""
    with _CACHE_LOCK:
        _SOURCE_CACHE.clear()
        _SESSION_CACHE.clear()

def session_no_from_slot(slot_id: str) -> int:
    """Пар№ — последняя цифра из slot.id. 'guap-mon-2' → 2."""
    digits = re.findall(r'\d', slot_id or "")
    return int(digits[-1]) if digits else 0