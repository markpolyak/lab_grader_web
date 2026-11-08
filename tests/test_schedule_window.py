from datetime import date, datetime, timezone

from grading.queue.schedule_window import (
    active_session_for,
    next_session_for,
    reset_schedule_cache,
    sessions_for_date,
)


def _write_schedule(path):
    path.write_text(
        """course-id: test-course
source: guap
timezone: UTC+3
auto_open_minutes_before: 15
auto_close_minutes_after: 10
groups:
  '4431':
    group_id: '4431'
    slots:
      - id: canceled-slot
        date: '2026-11-09'
        start: '10:00'
        end: '11:00'
      - id: lab1-slot
        date: '2026-11-09'
        start: '12:00'
        end: '13:00'
        labs: ['1']
      - id: lab2-slot
        date: '2026-11-09'
        start: '14:00'
        end: '15:00'
        labs: ['2']
    corrections:
      - id: cancel-and-add
        date: '2026-11-09'
        remove_slots:
          - slot_id: canceled-slot
        add_slots:
          - id: replacement-slot
            start: '16:00'
            end: '17:00'
            labs: ['3']
  '4432':
    group_id: '4432'
    timezone: Europe/Moscow
    slots:
      - id: monday-slot
        recurrence:
          day: monday
          week: all
        start: '10:00'
        end: '11:00'
        labs: ['1']
""",
        encoding="utf-8",
    )


def test_sessions_for_date_resolves_corrections_and_lab_allowlists(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    _write_schedule(schedule_path)
    reset_schedule_cache()

    sessions = sessions_for_date(
        "test-course", "4431", date(2026, 11, 9), tmp_path
    )

    assert [session.slot_id for session in sessions] == [
        "lab1-slot",
        "lab2-slot",
        "replacement-slot",
    ]
    assert active_session_for(
        "test-course", "4431", "1", datetime(2026, 11, 9, 12, 0), tmp_path
    ).slot_id == "lab1-slot"
    assert active_session_for(
        "test-course", "4431", "2", datetime(2026, 11, 9, 12, 0), tmp_path
    ) is None
    assert active_session_for(
        "test-course", "4431", "3", datetime(2026, 11, 9, 16, 0), tmp_path
    ).slot_id == "replacement-slot"


def test_active_session_converts_aware_now_to_group_timezone(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    _write_schedule(schedule_path)
    reset_schedule_cache()

    session = active_session_for(
        "test-course",
        "4432",
        "1",
        datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc),
        tmp_path,
    )

    assert session is not None
    assert session.slot_id == "monday-slot"


def test_next_session_ignores_sessions_that_do_not_allow_lab(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    _write_schedule(schedule_path)
    reset_schedule_cache()

    session = next_session_for(
        "test-course",
        "4431",
        "3",
        datetime(2026, 11, 9, 15, 0),
        tmp_path,
    )

    assert session is not None
    assert session.slot_id == "replacement-slot"
