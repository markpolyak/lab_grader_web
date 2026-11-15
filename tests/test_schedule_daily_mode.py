"""Daily-mode fallback tests (issue #62 / ADR point 5).

When a course has NO schedule YAML at all (not "file exists but misses this
group"), the queue lives as ONE window spanning the whole local day: every lab
is allowed and the synthetic slot_id has no digits, so session_no_from_slot ->
0 and all students land in a single shared queue for the group that day.

These tests complement test_queue_schedule_advanced.py, which covers the
"file exists" paths. Here we focus on the no-file path and the boundary
between "no file" (daily mode) and "file exists but group missing" (closed).
"""

from datetime import date, datetime, timezone

import pytest

from grading.queue import schedule_window as sw
from grading.queue.schedule_window import (
    active_session_for,
    next_session_for,
    reset_schedule_cache,
    session_no_from_slot,
    sessions_for_date,
)


# A minimal real schedule (group 4431 only) used to prove the file-exists /
# group-missing path still returns None (genuinely closed), distinct from the
# no-file daily-mode path. Format matches test_queue_schedule_advanced.py.
_SCHEDULE_WITH_4431 = """\
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
"""


@pytest.fixture
def no_schedule_dir(tmp_path):
    """tmp_path with no YAML for the queried course -> daily mode."""
    reset_schedule_cache()
    return tmp_path


@pytest.fixture
def schedule_with_4431(tmp_path):
    """tmp_path with a real YAML that only covers group 4431."""
    (tmp_path / "test-course.yaml").write_text(_SCHEDULE_WITH_4431, encoding="utf-8")
    reset_schedule_cache()
    return tmp_path


# ---------------------------------------------------------------------------
# No schedule file -> daily window (the fix for [P1] QUEUE_CLOSED)
# ---------------------------------------------------------------------------


def test_no_schedule_file_returns_daily_window(no_schedule_dir):
    """No YAML at all -> active_session_for returns a session, not None."""
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert session is not None
    assert session.allows_lab("1") is True


def test_no_schedule_file_does_not_parse(no_schedule_dir, monkeypatch):
    """The file-existence check must short-circuit before parse_course_schedule.

    Parsing is expensive and there is nothing to parse when the file is absent;
    if parse were called it would also blow up on a missing path in production.
    """
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("parse_course_schedule must not be called when the file is missing")

    monkeypatch.setattr(sw, "parse_course_schedule", _must_not_be_called)
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert session is not None


def test_daily_window_allows_any_lab(no_schedule_dir):
    """Daily mode: every lab is welcome in the single shared queue."""
    for lab in ("1", "2", "3", "anything"):
        session = active_session_for(
            "unscheduled-course", "4431", lab,
            datetime(2026, 11, 15, 12, 0), no_schedule_dir,
        )
        assert session is not None
        assert session.allows_lab(lab) is True


def test_daily_window_slot_has_pair_number_one(no_schedule_dir):
    """Дневная очередь — пара №1 дня: Пар№ в таблице не пустует
    (в режиме «очередь без расписания» иначе Пар№ оставался пустым)."""
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert session is not None
    assert session.slot_id == "daily-1"
    assert session_no_from_slot(session.slot_id) == 1


@pytest.mark.parametrize(
    "hour, minute",
    [(0, 0), (3, 0), (12, 0), (18, 30), (23, 59)],
)
def test_daily_window_is_active_all_day(no_schedule_dir, hour, minute):
    """The whole-day window is open from 00:00 to 23:59 local."""
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, hour, minute), no_schedule_dir,
    )
    assert session is not None
    assert session.auto_open_at <= datetime(2026, 11, 15, hour, minute) <= session.auto_close_at


def test_daily_window_spans_midnight_to_midnight(no_schedule_dir):
    """auto_open_at = today 00:00, auto_close_at = tomorrow 00:00."""
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert session is not None
    assert session.auto_open_at == datetime(2026, 11, 15, 0, 0)
    assert session.auto_close_at == datetime(2026, 11, 16, 0, 0)
    assert session.starts_at == datetime(2026, 11, 15, 0, 0)
    assert session.ends_at == datetime(2026, 11, 16, 0, 0)


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


def test_daily_window_respects_course_timezone(no_schedule_dir):
    """timezone_str controls which local day the window spans.

    UTC 10:00 with timezone UTC+5 -> local 15:00 on Nov 15 -> the window is
    Nov 15 00:00 .. Nov 16 00:00 in UTC+5 (naive local datetimes).
    """
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc),
        no_schedule_dir,
        timezone_str="UTC+5",
    )
    assert session is not None
    assert session.auto_open_at == datetime(2026, 11, 15, 0, 0)
    assert session.auto_close_at == datetime(2026, 11, 16, 0, 0)


def test_daily_window_bad_timezone_falls_back_to_moscow(no_schedule_dir):
    """A bogus timezone must not break the daily-mode contract."""
    session = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
        timezone_str="Not/A/Zone",
    )
    assert session is not None
    assert session.allows_lab("1") is True


# ---------------------------------------------------------------------------
# Cache (one entry per course+group+day instead of many per-pair windows)
# ---------------------------------------------------------------------------


def test_daily_window_cached_for_same_day(no_schedule_dir):
    """Repeated lookups on the same day return the SAME cached object."""
    a = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 9, 0), no_schedule_dir,
    )
    b = active_session_for(
        "unscheduled-course", "4431", "2",  # different lab, same day
        datetime(2026, 11, 15, 21, 0), no_schedule_dir,
    )
    assert a is not None and b is not None
    assert a is b


def test_daily_window_different_day_yields_different_window(no_schedule_dir):
    """A new local day produces a new window object (and prunes the old one)."""
    day1 = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    day2 = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 16, 12, 0), no_schedule_dir,
    )
    assert day1 is not day2
    assert day1.auto_open_at == datetime(2026, 11, 15, 0, 0)
    assert day2.auto_open_at == datetime(2026, 11, 16, 0, 0)


def test_daily_cache_cleared_by_reset(no_schedule_dir):
    """reset_schedule_cache() must drop daily fallbacks (test isolation)."""
    first = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    reset_schedule_cache()
    second = active_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert first is not second


# ---------------------------------------------------------------------------
# next_session_for / sessions_for_date in daily mode
# ---------------------------------------------------------------------------


def test_next_session_none_in_daily_mode(no_schedule_dir):
    """No 'next session' concept when the queue is open all day every day."""
    session = next_session_for(
        "unscheduled-course", "4431", "1",
        datetime(2026, 11, 15, 12, 0), no_schedule_dir,
    )
    assert session is None


def test_sessions_for_date_empty_in_daily_mode(no_schedule_dir):
    """The admin schedule listing shows no sessions when there is no YAML.

    Daily mode is an implicit queue behaviour, not a scheduled session, so the
    listing stays empty (unchanged from the pre-fix behaviour).
    """
    sessions = sessions_for_date(
        "unscheduled-course", "4431", date(2026, 11, 15), no_schedule_dir,
    )
    assert sessions == []


# ---------------------------------------------------------------------------
# Boundary: file exists but group missing -> genuinely closed (unchanged)
# ---------------------------------------------------------------------------


def test_file_exists_but_group_missing_returns_none(schedule_with_4431):
    """A YAML that covers group 4431 must NOT trigger daily mode for group 9999."""
    session = active_session_for(
        "test-course", "9999", "1",
        datetime(2026, 11, 9, 11, 0), schedule_with_4431,
    )
    assert session is None


def test_file_exists_group_missing_next_session_none(schedule_with_4431):
    session = next_session_for(
        "test-course", "9999", "1",
        datetime(2026, 11, 9, 11, 0), schedule_with_4431,
    )
    assert session is None
