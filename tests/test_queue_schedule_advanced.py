"""Advanced schedule tests: session_no extraction, multi-day, timezone edge cases."""

import re
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path

import pytest

from grading.queue.schedule_window import (
    active_session_for,
    next_session_for,
    reset_schedule_cache,
    session_no_from_slot,
    sessions_for_date,
)
from grading.schedule import ScheduleConfigError


def _write_schedule(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


SCHEDULE_YAML = """\
course-id: test-course
source: guap
timezone: UTC+3
auto_open_minutes_before: 15
auto_close_minutes_after: 10
groups:
  '4431':
    group_id: '4431'
    slots:
      - id: guap-mon-2
        date: '2026-11-09'
        start: '10:40'
        end: '12:10'
        labs: ['1']
      - id: guap-mon-4
        date: '2026-11-09'
        start: '14:40'
        end: '16:10'
        labs: ['2']
      - id: guap-mon-6
        date: '2026-11-09'
        start: '18:00'
        end: '19:30'
  '4432':
    group_id: '4432'
    timezone: Europe/Moscow
    slots:
      - id: guap-tue-3
        recurrence:
          day: monday
          week: all
        start: '13:00'
        end: '14:30'
        labs: ['1']
"""


@pytest.fixture
def schedule_dir(tmp_path):
    _write_schedule(tmp_path / "test-course.yaml", SCHEDULE_YAML)
    reset_schedule_cache()
    return tmp_path


# ---------------------------------------------------------------------------
# session_no_from_slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slot_id, expected",
    [
        ("guap-mon-2", 2),
        ("guap-mon-4", 4),
        ("guap-mon-6", 6),
        ("guap-tue-3", 3),
        ("extra-lab3", 3),
        ("no-number", 0),
        ("", 0),
        (None, 0),
        ("lab-0", 0),
        # Per the docstring ("последняя цифра") the function takes the LAST
        # single digit, not the whole number: "lab-10" -> 0, "lab-99" -> 9.
        # Multi-digit slot_ids are not a real GUAP convention (pairs are 1-8).
        ("lab-10", 0),
        ("lab-99", 9),
    ],
)
def test_session_no_from_slot(slot_id, expected):
    assert session_no_from_slot(slot_id) == expected


# ---------------------------------------------------------------------------
# Active session window
# ---------------------------------------------------------------------------


def test_active_session_at_exact_open_time(schedule_dir):
    """auto_open_at = start - 15 min. At that exact moment, session is open."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 10, 25),  # 10:40 - 15 min = 10:25
        schedule_dir,
    )
    assert session is not None
    assert session.slot_id == "guap-mon-2"


def test_active_session_at_exact_close_time(schedule_dir):
    """auto_close_at = end + 10 min. At that exact moment, session is still open."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 12, 20),  # 12:10 + 10 min = 12:20
        schedule_dir,
    )
    assert session is not None


def test_active_session_just_before_open_returns_none(schedule_dir):
    """1 minute before auto_open_at → session not yet open."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 10, 24),
        schedule_dir,
    )
    assert session is None


def test_active_session_just_after_close_returns_none(schedule_dir):
    """1 minute after auto_close_at → session closed."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 12, 21),
        schedule_dir,
    )
    assert session is None


def test_active_session_filters_by_lab(schedule_dir):
    """lab 1 is only in guap-mon-2; lab 2 is only in guap-mon-4."""
    # During session 2 (10:40-12:10), lab 2 is not allowed
    session = active_session_for(
        "test-course", "4431", "2",
        datetime(2026, 11, 9, 11, 0),
        schedule_dir,
    )
    assert session is None

    # During session 4 (14:40-16:10), lab 1 is not allowed
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 15, 0),
        schedule_dir,
    )
    assert session is None

    # During session 6 (18:00-19:30), no labs filter → all labs allowed
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 18, 30),
        schedule_dir,
    )
    assert session is not None
    assert session.slot_id == "guap-mon-6"

    session = active_session_for(
        "test-course", "4431", "2",
        datetime(2026, 11, 9, 18, 30),
        schedule_dir,
    )
    assert session is not None


# ---------------------------------------------------------------------------
# Timezone conversion
# ---------------------------------------------------------------------------


def test_aware_datetime_converted_to_course_timezone(schedule_dir):
    """UTC 7:00 → UTC+3 = 10:00, which is before session 2 (starts 10:40)."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc),
        schedule_dir,
    )
    assert session is None


def test_aware_datetime_at_session_start(schedule_dir):
    """UTC 7:40 → UTC+3 = 10:40 = session start → open (auto_open is 10:25)."""
    session = active_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 7, 40, tzinfo=timezone.utc),
        schedule_dir,
    )
    assert session is not None


def test_group_with_iana_timezone(schedule_dir):
    """Group 4432 uses Europe/Moscow (IANA name, not UTC+N)."""
    # Monday 13:00 Moscow = 10:00 UTC
    session = active_session_for(
        "test-course", "4432", "1",
        datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc),
        schedule_dir,
    )
    assert session is not None
    assert session.slot_id == "guap-tue-3"


# ---------------------------------------------------------------------------
# Next session
# ---------------------------------------------------------------------------


def test_next_session_returns_earliest_future(schedule_dir):
    """At 12:30 (after session 2), next is session 4 at 14:40."""
    session = next_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 12, 30),
        schedule_dir,
    )
    # lab 1 is not allowed in session 4; session 6 has no lab filter
    assert session is not None
    assert session.slot_id == "guap-mon-6"


def test_next_session_with_lab_filter_skips_disallowed(schedule_dir):
    """At 12:30, looking for lab 2: next is session 4 (14:40)."""
    session = next_session_for(
        "test-course", "4431", "2",
        datetime(2026, 11, 9, 12, 30),
        schedule_dir,
    )
    assert session is not None
    assert session.slot_id == "guap-mon-4"


def test_next_session_returns_none_if_no_future_sessions(schedule_dir):
    """After the last session of the day, next_session returns None (within 8 days)."""
    # 2026-11-09 is the only date with sessions; 8 days from Nov 9 = Nov 17
    # Monday Nov 16 has session for 4432, but not for 4431
    session = next_session_for(
        "test-course", "4431", "1",
        datetime(2026, 11, 9, 20, 0),  # after all sessions
        schedule_dir,
    )
    assert session is None


# ---------------------------------------------------------------------------
# Multiple groups independence
# ---------------------------------------------------------------------------


def test_different_groups_have_different_sessions(schedule_dir):
    """Group 4431 has sessions on Nov 9; group 4432 has recurring Monday sessions."""
    sessions_4431 = sessions_for_date(
        "test-course", "4431", date(2026, 11, 9), schedule_dir
    )
    sessions_4432 = sessions_for_date(
        "test-course", "4432", date(2026, 11, 9), schedule_dir
    )

    assert len(sessions_4431) == 3
    assert len(sessions_4432) == 1
    assert sessions_4431[0].group_id == "4431"
    assert sessions_4432[0].group_id == "4432"


# ---------------------------------------------------------------------------
# Missing schedule file
# ---------------------------------------------------------------------------


def test_missing_schedule_returns_empty_sessions(tmp_path):
    reset_schedule_cache()
    sessions = sessions_for_date(
        "no-such-course", "4431", date(2026, 11, 9), tmp_path
    )
    assert sessions == []


def test_missing_schedule_falls_back_to_daily_window(tmp_path):
    """No schedule file at all -> daily mode: one whole-day window.

    Issue #62 / ADR point 5: "Если расписания для курса нет -- очередь живёт в
    течение дня." The synthetic window allows any lab and its slot_id carries no
    digits, so session_no_from_slot -> 0 (one shared queue for the group).
    """
    reset_schedule_cache()
    session = active_session_for(
        "no-such-course", "4431", "1",
        datetime(2026, 11, 9, 12, 0),
        tmp_path,
    )
    assert session is not None
    assert session.allows_lab("1") is True
    # Дневная очередь — пара №1 дня: Пар№ в таблице не пустует.
    assert session.slot_id == "daily-1"
    assert session_no_from_slot(session.slot_id) == 1


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def test_schedule_cache_invalidation_on_file_change(schedule_dir):
    """Changing the schedule file mtime triggers a cache miss."""
    # First read populates cache
    sessions1 = sessions_for_date(
        "test-course", "4431", date(2026, 11, 9), schedule_dir
    )
    assert len(sessions1) == 3

    # Modify the file
    schedule_file = schedule_dir / "test-course.yaml"
    original = schedule_file.read_text(encoding="utf-8")
    modified = original.replace("10:40", "09:00")
    schedule_file.write_text(modified, encoding="utf-8")

    # Touch mtime
    import os
    stat = os.stat(schedule_file)
    os.utime(schedule_file, (stat.st_atime + 1, stat.st_mtime + 1))

    # Second read should pick up the change
    sessions2 = sessions_for_date(
        "test-course", "4431", date(2026, 11, 9), schedule_dir
    )
    assert sessions2[0].starts_at.hour == 9  # was 10, now 9
