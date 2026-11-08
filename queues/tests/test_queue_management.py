from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from queues.domain import DefenseResult, EntryStatus, Student
from queues.domain.err import (
    InvalidStateError,
    QueueNotDeletableError,
    QueueNotEmptyError,
)
from queues.repositories.storage.file import FileQueueRepository
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
COURSES = {"os-2026": {"name": "Операционные системы", "university": "ГУАП"}}
SCHEDULE_DIR = Path(__file__).resolve().parents[2] / "schedule"
TZ = timezone(timedelta(hours=3))
NOW = datetime(2026, 11, 9, 14, 50, tzinfo=TZ)


def build(tmp_path) -> QueueService:
    service = QueueService(FileQueueRepository(tmp_path / "queue_state"))
    service.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY)
    return service


def create_manual(service, *, closed=False):
    return service.create_manual(
        "os-2026",
        "Операционные системы",
        week_start=MONDAY,
        group_ids=["4431"],
        starts_at=datetime(2026, 11, 9, 20, 0, tzinfo=TZ),
        ends_at=datetime(2026, 11, 9, 21, 30, tzinfo=TZ),
        registration_opens_at=datetime(2026, 11, 9, 19, 45, tzinfo=TZ),
        registration_closes_at=datetime(2026, 11, 9, 21, 30, tzinfo=TZ),
        service_until=datetime(2026, 11, 9, 23, 59, tzinfo=TZ),
        closed=closed,
    )


def add_waiting(service, lesson, github="student"):
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", github), "1", "ЛР1"
    )
    service.save_lesson(lesson.id)
    return entry


def test_manual_queue_is_created_and_restored_after_restart(tmp_path):
    service = build(tmp_path)
    record = create_manual(service)

    assert record.manual is True
    lesson = next(
        item for item in service.schedules["os-2026"].lessons if item.id == record.id
    )
    assert lesson.kind == "Внештатное занятие"
    assert lesson.groups == ("4431",)
    assert lesson.queue.closed is False

    restored = build(tmp_path)
    lessons = {item.id: item for item in restored.schedules["os-2026"].lessons}
    assert record.id in lessons
    assert lessons[record.id].kind == "Внештатное занятие"
    assert lessons[record.id].queue.closed is False
    assert restored.get_record(record.id).manual is True


def test_update_queue_changes_times_and_registration_window(tmp_path):
    service = build(tmp_path)
    record = create_manual(service)
    starts_at = datetime(2026, 11, 9, 20, 30, tzinfo=TZ)
    ends_at = datetime(2026, 11, 9, 22, 0, tzinfo=TZ)
    closes_at = datetime(2026, 11, 9, 22, 0, tzinfo=TZ)

    updated = service.update_queue(
        record.id,
        starts_at=starts_at,
        ends_at=ends_at,
        registration_closes_at=closes_at,
    )

    assert updated.lesson_starts_at == starts_at
    assert updated.lesson_ends_at == ends_at
    assert updated.registration_closes_at == closes_at
    assert updated.version == 1

    restored = build(tmp_path)
    lesson = next(
        item for item in restored.schedules["os-2026"].lessons if item.id == record.id
    )
    assert lesson.starts_at == starts_at
    assert lesson.ends_at == ends_at


def test_update_queue_rejects_invalid_times(tmp_path):
    service = build(tmp_path)
    record = create_manual(service)

    with pytest.raises(ValueError):
        service.update_queue(
            record.id,
            ends_at=datetime(2026, 11, 9, 19, 0, tzinfo=TZ),
        )


def test_update_queue_rejects_scheduled_lesson_times(tmp_path):
    service = build(tmp_path)
    scheduled = service.schedules["os-2026"].lessons[0]

    with pytest.raises(ValueError):
        service.update_queue(
            scheduled.id,
            starts_at=datetime(2026, 11, 9, 16, 0, tzinfo=TZ),
        )


def test_manual_queue_must_be_in_the_current_week(tmp_path):
    service = build(tmp_path)

    with pytest.raises(ValueError):
        service.create_manual(
            "os-2026",
            "Операционные системы",
            week_start=MONDAY,
            group_ids=["4431"],
            starts_at=datetime(2026, 11, 16, 20, 0, tzinfo=TZ),
            ends_at=datetime(2026, 11, 16, 21, 30, tzinfo=TZ),
        )


def test_delete_manual_empty_queue(tmp_path):
    service = build(tmp_path)
    record = create_manual(service)

    service.delete_queue(record.id)

    assert service.get_record(record.id) is None
    assert all(lesson.id != record.id for lesson in service.schedules["os-2026"].lessons)
    assert FileQueueRepository(tmp_path / "queue_state").get(record.id) is None

    restored = build(tmp_path)
    assert all(
        lesson.id != record.id for lesson in restored.schedules["os-2026"].lessons
    )


def test_delete_rejects_scheduled_or_non_empty_queue(tmp_path):
    service = build(tmp_path)
    scheduled = service.schedules["os-2026"].lessons[0]
    with pytest.raises(QueueNotDeletableError):
        service.delete_queue(scheduled.id)

    record = create_manual(service)
    lesson = next(
        item for item in service.schedules["os-2026"].lessons if item.id == record.id
    )
    add_waiting(service, lesson)
    with pytest.raises(QueueNotEmptyError):
        service.delete_queue(record.id)


def test_remove_entry_marks_withdrawn_and_keeps_history(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]
    entry = add_waiting(service, lesson)

    service.remove_entry(lesson.id, entry.id, now=NOW)

    record = service.get_record(lesson.id)
    assert record.entry_metadata[entry.id].status == EntryStatus.WITHDRAWN
    assert service.active_entry("os-2026", "student") is None
    with pytest.raises(InvalidStateError):
        service.remove_entry(lesson.id, entry.id, now=NOW)

    items, _ = service.history("os-2026")
    assert [item["github"] for item in items] == ["student"]
    assert items[0]["status"] == EntryStatus.WITHDRAWN


def test_update_result_corrects_finished_entry_only(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]
    entry = add_waiting(service, lesson)
    service.call_next(lesson.id, now=NOW)
    service.finish(lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW)

    service.update_result(lesson.id, entry.id, DefenseResult.NOT_DEFENDED)

    record = service.get_record(lesson.id)
    assert record.entry_metadata[entry.id].result == DefenseResult.NOT_DEFENDED
    items, _ = service.history("os-2026")
    assert items[0]["result"] == DefenseResult.NOT_DEFENDED

    waiting = add_waiting(service, lesson, github="other")
    with pytest.raises(InvalidStateError):
        service.update_result(lesson.id, waiting.id, DefenseResult.DEFENDED)
