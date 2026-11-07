from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from grading.queue_eligibility import EligibleStudentLab
from queues.domain import DefenseResult, EntryStatus, Student
from queues.domain.err import (
    AlreadyQueuedError,
    EntryNotFoundError,
    EntryNotOwnedError,
    GroupNotAllowedError,
    InvalidStateError,
    QueueClosedError,
    QueueEmptyError,
)
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
COURSES = {"os-2026": {"name": "Операционные системы", "university": "ГУАП"}}
SCHEDULE_DIR = Path(__file__).resolve().parents[2] / "schedule"
NOW = datetime(2026, 11, 9, 14, 50, tzinfo=timezone(timedelta(hours=3)))


def build() -> QueueService:
    service = QueueService()
    service.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY)
    return service


def eligible(github="student", *, group_id="4431", lab_id="1", lab_name="ЛР1"):
    return EligibleStudentLab(
        student=Student("Иван", "Иванов", "", github),
        full_name="Иванов Иван",
        group_id=group_id,
        lab_id=lab_id,
        lab_name=lab_name,
    )


def open_first_lesson(service):
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    return lesson


def test_join_creates_a_waiting_entry_and_saves_it():
    service = build()
    lesson = open_first_lesson(service)

    entry = service.join(lesson.id, eligible(), now=NOW)

    record = service.get_record(lesson.id)
    assert record.entry_metadata[entry.id].status == EntryStatus.WAITING
    assert record.version == 0
    assert service.active_entry("os-2026", "student") == (record, entry)


def test_join_rejects_closed_queue():
    service = build()
    lesson = service.schedules["os-2026"].lessons[0]

    with pytest.raises(QueueClosedError):
        service.join(lesson.id, eligible(), now=NOW)


def test_join_rejects_a_group_not_in_the_lesson():
    service = build()
    lesson = open_first_lesson(service)

    with pytest.raises(GroupNotAllowedError):
        service.join(lesson.id, eligible(group_id="9999"), now=NOW)


def test_join_rejects_a_second_active_entry_in_the_course():
    service = build()
    lesson = open_first_lesson(service)
    service.join(lesson.id, eligible(), now=NOW)

    with pytest.raises(AlreadyQueuedError):
        service.join(lesson.id, eligible(lab_id="2", lab_name="ЛР2"), now=NOW)


def test_call_next_and_finish_move_through_states():
    service = build()
    lesson = open_first_lesson(service)
    first = service.join(lesson.id, eligible("first"), now=NOW)
    second = service.join(lesson.id, eligible("second"), now=NOW + timedelta(minutes=1))

    called = service.call_next(lesson.id, now=NOW + timedelta(minutes=2))
    assert called.id == first.id
    record = service.get_record(lesson.id)
    assert record.entry_metadata[first.id].status == EntryStatus.CALLED
    assert record.entry_metadata[first.id].called_at == NOW + timedelta(minutes=2)

    service.finish(
        lesson.id, first.id, DefenseResult.DEFENDED, now=NOW + timedelta(minutes=3)
    )
    assert record.entry_metadata[first.id].status == EntryStatus.FINISHED
    assert record.entry_metadata[first.id].result == DefenseResult.DEFENDED

    assert service.call_next(lesson.id, now=NOW + timedelta(minutes=4)).id == second.id


def test_call_next_reports_empty_queue():
    service = build()
    lesson = open_first_lesson(service)

    with pytest.raises(QueueEmptyError):
        service.call_next(lesson.id, now=NOW)


def test_finish_requires_a_called_entry():
    service = build()
    lesson = open_first_lesson(service)
    entry = service.join(lesson.id, eligible(), now=NOW)

    with pytest.raises(InvalidStateError):
        service.finish(lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW)


def test_leave_marks_withdrawn_and_allows_rejoining():
    service = build()
    lesson = open_first_lesson(service)
    entry = service.join(lesson.id, eligible(), now=NOW)

    service.leave("os-2026", entry.id, "student", now=NOW + timedelta(minutes=1))
    record = service.get_record(lesson.id)
    assert record.entry_metadata[entry.id].status == EntryStatus.WITHDRAWN
    assert service.active_entry("os-2026", "student") is None

    again = service.join(lesson.id, eligible(lab_id="2", lab_name="ЛР2"), now=NOW + timedelta(minutes=2))
    assert again.student.github == "student"
    assert record.entry_metadata[again.id].status == EntryStatus.WAITING


def test_leave_rejects_foreign_or_unknown_entry():
    service = build()
    lesson = open_first_lesson(service)
    entry = service.join(lesson.id, eligible(), now=NOW)

    with pytest.raises(EntryNotOwnedError):
        service.leave("os-2026", entry.id, "other", now=NOW)
    with pytest.raises(EntryNotFoundError):
        service.leave("os-2026", uuid4(), "student", now=NOW + timedelta(minutes=1))
    assert service.get_record(lesson.id).entry_metadata[entry.id].status == EntryStatus.WAITING


def test_history_returns_terminal_entries_and_filters():
    service = build()
    lesson = open_first_lesson(service)
    finished = service.join(lesson.id, eligible("finished"), now=NOW)
    withdrawn = service.join(lesson.id, eligible("withdrawn"), now=NOW + timedelta(minutes=1))

    service.call_next(lesson.id, now=NOW + timedelta(minutes=2))
    service.finish(
        lesson.id, finished.id, DefenseResult.DEFENDED,
        now=NOW + timedelta(minutes=3),
    )
    service.leave(
        "os-2026", withdrawn.id, "withdrawn",
        now=NOW + timedelta(minutes=4),
    )

    items, next_offset = service.history("os-2026")
    assert next_offset is None
    assert [item["github"] for item in items] == ["withdrawn", "finished"]
    assert items[0]["status"] == EntryStatus.WITHDRAWN
    assert items[1]["result"] == DefenseResult.DEFENDED

    filtered, _ = service.history("os-2026", group_id="4431")
    assert len(filtered) == 2
    assert service.history("os-2026", group_id="9999")[0] == []

    page, next_offset = service.history("os-2026", limit=1, offset=0)
    assert len(page) == 1
    assert next_offset == 1
    page, next_offset = service.history("os-2026", limit=1, offset=1)
    assert len(page) == 1
    assert next_offset is None
