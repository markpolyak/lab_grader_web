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
# Daily-mode fallback sessions (no schedule YAML exists for the course at all).
# Keyed by (course_id, group_id, local_date, timezone_str) -> _WholeDaySession.
# One entry per (course, group, day) instead of the usual many per-pair windows.
_DAILY_FALLBACK_CACHE: dict[tuple[str, str, date, str], "_WholeDaySession"] = {}
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


def _safe_timezone(value: str | None) -> timezone | ZoneInfo:
    """Timezone for the daily fallback; never raises -- falls back to Moscow.

    The course timezone comes from course_info (main.py) and is already trusted
    elsewhere (``_queue_today_str`` falls back to UTC on a bad value); a bad
    timezone must not break the "no schedule -> queue open all day" contract.
    """
    if value:
        try:
            return _timezone_for(value)
        except ScheduleConfigError:
            pass
    return _timezone_for("Europe/Moscow")


def _local_now(tz: timezone | ZoneInfo, now: datetime | None) -> datetime:
    """Current local naive datetime in ``tz`` (or use ``now`` if supplied)."""
    if now is None:
        return datetime.now(tz).replace(tzinfo=None)
    if now.tzinfo is None:
        return now
    return now.astimezone(tz).replace(tzinfo=None)


def _load_source(
    course_id: str,
    group_id: str,
    schedules_dir: str | Path,
):
    path = schedule_path_for_course(course_id, schedules_dir)
    if not path.is_file():
        # File does not exist AT ALL. Distinguish this from "file exists but the
        # group is not in it": here we return modified_ns=None so callers can
        # fall back to the daily mode (one whole-day window). A None modified_ns
        # is impossible for an existing file (stat().st_mtime_ns is always >= 0).
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
    return ScheduleContext(source, local_timezone, _local_now(local_timezone, now))


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


# --- Daily mode: no schedule YAML -------------------------------------------
#
# Issue #62 / ADR point 5: "Если расписания для курса нет -- очередь живёт в
# течение дня." When the schedule file does not exist AT ALL (not "exists but
# misses this group"), the queue runs as ONE window spanning the whole local
# day. Every lab is allowed (allows_lab -> True) and the synthetic slot_id has
# no digits, so session_no_from_slot -> 0: all students land in a single shared
# queue for the group that day, instead of one queue per pair.
#
# The session is a small stand-in dataclass, duck-type compatible with
# ScheduledSession (main.py only reads attributes and calls allows_lab). It is
# always "active" for the current local day by construction:
#   auto_open_at = today 00:00, auto_close_at = tomorrow 00:00, and
#   context.now is always within [today 00:00, tomorrow 00:00].
# It is cached per (course, group, day, timezone) -- one entry per day instead
# of the many per-pair windows a real schedule would produce.

# "1" на конце даёт session_no_from_slot -> 1: дневная очередь — это
# пара №1 дня, Пар№ в таблице не пустует (issue #62).
_DAILY_SLOT_ID = "daily-1"


@dataclass(frozen=True)
class _WholeDaySession:
    """Synthetic whole-day session used when no schedule YAML exists."""

    slot_id: str
    subject: str
    starts_at: datetime
    ends_at: datetime
    auto_open_at: datetime
    auto_close_at: datetime
    room: str
    teacher: str
    note: str
    allowed_lab_ids: list[str] | None  # None == all labs (matches ScheduledSession)
    removed_lab_ids: list[str]

    def allows_lab(self, lab_key: str) -> bool:
        # Daily mode: no schedule -> every lab is welcome in the single queue.
        return True


def _daily_fallback_session(
    course_id: str,
    group_id: str,
    now: datetime | None,
    timezone_str: str | None,
) -> _WholeDaySession:
    """Build (and cache) the whole-day session for the no-schedule case."""
    tz = _safe_timezone(timezone_str)
    local_now = _local_now(tz, now)
    today = local_now.date()
    tz_key = timezone_str or "Europe/Moscow"
    cache_key = (course_id, group_id, today, tz_key)
    with _CACHE_LOCK:
        cached = _DAILY_FALLBACK_CACHE.get(cache_key)
        if cached is not None:
            return cached

    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    session = _WholeDaySession(
        slot_id=_DAILY_SLOT_ID,
        subject="",
        starts_at=day_start,
        ends_at=day_end,
        auto_open_at=day_start,
        auto_close_at=day_end,
        room="",
        teacher="",
        note="Очередь работает весь день (расписание не задано)",
        allowed_lab_ids=None,  # None == all labs
        removed_lab_ids=[],
    )
    with _CACHE_LOCK:
        # Drop stale entries for this course+group (other days/tz) so the cache
        # stays bounded to one entry per active day.
        stale = [
            key for key in _DAILY_FALLBACK_CACHE
            if key[:2] == (course_id, group_id) and key != cache_key
        ]
        for key in stale:
            _DAILY_FALLBACK_CACHE.pop(key, None)
        _DAILY_FALLBACK_CACHE[cache_key] = session
    return session


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
    timezone_str: str | None = None,
) -> ScheduledSession | _WholeDaySession | None:
    """Find an open session allowing this lab on the group's local date.

    When the schedule YAML does not exist AT ALL for the course, falls back to
    a single whole-day window (issue #62 / ADR point 5) so the queue lives for
    the whole day and every lab may register. Pass ``timezone_str`` (the course
    timezone) so the window's displayed starts_at/ends_at match the course; if
    omitted, Europe/Moscow is used. The fallback is always active by
    construction, so it never returns None for the no-file case.
    """
    source, modified_ns = _load_source(course_id, group_id, schedules_dir)
    if source is None:
        if modified_ns is None:
            # No schedule file at all -> daily mode.
            return _daily_fallback_session(course_id, group_id, now, timezone_str)
        # File exists but this group is not scheduled -> genuinely closed.
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
        and session.allows_lab(lab_key)
    ]
    return min(active, key=lambda session: session.starts_at) if active else None


def next_session_for(
    course_id: str,
    group_id: str,
    lab_key: str,
    now: datetime | None = None,
    schedules_dir: str | Path = "schedules",
) -> ScheduledSession | None:
    """Find the next future session allowing this lab within eight days.

    In daily mode (no schedule file) there is no concept of a "next" session --
    the queue is open all day every day -- so None is returned, matching the
    pre-existing behaviour. ``active_session_for`` already reports the window as
    open, so the "next session" hint is irrelevant when the queue is open.
    """
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
        and session.allows_lab(lab_key)
    ]
    return min(upcoming, key=lambda session: session.starts_at) if upcoming else None


def reset_schedule_cache() -> None:
    """Clear schedule caches for tests and explicit configuration reloads."""
    with _CACHE_LOCK:
        _SOURCE_CACHE.clear()
        _SESSION_CACHE.clear()
        _DAILY_FALLBACK_CACHE.clear()

def session_no_from_slot(slot_id: str) -> int:
    """Пар№ — последняя цифра из slot.id. 'guap-mon-2' → 2."""
    digits = re.findall(r'\d', slot_id or "")
    return int(digits[-1]) if digits else 0
