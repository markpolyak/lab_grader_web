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


def _write_partial_cancel_schedule(path):
    """Review defect #13 repro: a slot that allows ALL labs (no `labs` key)
    and a partial correction removing one lab from it. queue_settings
    declares no all_lab_ids, so there is no list to subtract from."""
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
      - id: guap-mon-2
        date: '2026-11-09'
        start: '10:40'
        end: '12:10'
    corrections:
      - id: cancel-lab3
        date: '2026-11-09'
        remove_slots:
          - slot_id: guap-mon-2
            labs: ['3']
""",
        encoding="utf-8",
    )


def test_partial_cancel_excludes_lab_from_all_labs_slot(tmp_path):
    # ЛР1 остаётся разрешена, отменённая ЛР3 закрыта — без all_lab_ids.
    schedule_path = tmp_path / "test-course.yaml"
    _write_partial_cancel_schedule(schedule_path)
    reset_schedule_cache()

    session = active_session_for(
        "test-course", "4431", "1", datetime(2026, 11, 9, 12, 0), tmp_path
    )
    assert session is not None
    assert session.slot_id == "guap-mon-2"
    assert session.allowed_lab_ids is None       # still "all labs"
    assert session.removed_lab_ids == ["3"]      # except the canceled one

    # Отменённая ЛР3: активной сессии нет -> очередь вернёт QUEUE_CLOSED.
    assert active_session_for(
        "test-course", "4431", "3", datetime(2026, 11, 9, 12, 0), tmp_path
    ) is None
    # ... и в ближайших парах она тоже не всплывает.
    assert next_session_for(
        "test-course", "4431", "3", datetime(2026, 11, 9, 9, 0), tmp_path
    ) is None


def test_partial_cancel_accumulates_across_corrections(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    _write_partial_cancel_schedule(schedule_path)
    schedule_path.write_text(
        schedule_path.read_text(encoding="utf-8").replace(
            "            labs: ['3']",
            "            labs: ['3']\n      - id: cancel-lab2\n"
            "        date: '2026-11-09'\n        remove_slots:\n"
            "          - slot_id: guap-mon-2\n            labs: ['2']",
        ),
        encoding="utf-8",
    )
    reset_schedule_cache()

    sessions = sessions_for_date("test-course", "4431", date(2026, 11, 9), tmp_path)
    assert sessions[0].removed_lab_ids == ["2", "3"]
    assert active_session_for(
        "test-course", "4431", "2", datetime(2026, 11, 9, 12, 0), tmp_path
    ) is None
    assert active_session_for(
        "test-course", "4431", "1", datetime(2026, 11, 9, 12, 0), tmp_path
    ) is not None


def test_partial_cancel_still_subtracts_explicit_lab_lists(tmp_path):
    # Регрессия: при явно перечисленных labs вычитание из списка работает,
    # исключения не появляются.
    schedule_path = tmp_path / "test-course.yaml"
    _write_partial_cancel_schedule(schedule_path)
    schedule_path.write_text(
        schedule_path.read_text(encoding="utf-8").replace(
            "        start: '10:40'",
            "        start: '10:40'\n        labs: ['1', '3']",
        ),
        encoding="utf-8",
    )
    reset_schedule_cache()

    session = active_session_for(
        "test-course", "4431", "1", datetime(2026, 11, 9, 12, 0), tmp_path
    )
    assert session.allowed_lab_ids == ["1"]
    assert session.removed_lab_ids is None
    assert active_session_for(
        "test-course", "4431", "3", datetime(2026, 11, 9, 12, 0), tmp_path
    ) is None
