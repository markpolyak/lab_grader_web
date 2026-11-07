from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from queues.domain import EntryStatus, Student
from queues.repositories.storage.file import FileQueueRepository
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
COURSES = {"os-2026": {"name": "Операционные системы", "university": "ГУАП"}}
SCHEDULE_DIR = Path(__file__).resolve().parents[2] / "schedule"


def build(tmp_path) -> QueueService:
    service = QueueService(FileQueueRepository(tmp_path / "queue_state"))
    service.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY)
    return service


def state_files(tmp_path):
    return list((tmp_path / "queue_state").glob("*.yaml"))


def test_new_lesson_is_not_written_until_saved(tmp_path):
    service = build(tmp_path)
    assert state_files(tmp_path) == []

    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    lesson.queue.add_entry(Student("Иван", "Иванов", "", "student"), "1", "ЛР1")

    record = service.save_lesson(lesson.id)

    assert record.version == 0
    assert len(state_files(tmp_path)) == 1


def test_saved_state_is_restored_by_a_new_service(tmp_path):
    first = build(tmp_path)
    lesson = first.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(Student("Иван", "Иванов", "", "student"), "1", "ЛР1")
    first.save_lesson(lesson.id)

    second = build(tmp_path)
    restored = next(
        item for item in second.schedules["os-2026"].lessons if item.id == lesson.id
    )

    assert restored.queue.closed is False
    assert restored.queue.get_position(github="student") == 1
    assert restored.queue.entries[0].id == entry.id
    assert second._records[lesson.id].entry_metadata[entry.id].status == EntryStatus.WAITING


def test_save_lesson_increments_version(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]

    assert service.save_lesson(lesson.id).version == 0
    assert service.save_lesson(lesson.id).version == 1

    stored = FileQueueRepository(tmp_path / "queue_state").get(lesson.id)
    assert stored.version == 1


def test_refresh_restores_persisted_state(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    lesson.queue.add_entry(Student("Иван", "Иванов", "", "student"), "1", "ЛР1")
    service.save_lesson(lesson.id)

    service.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY)
    refreshed = next(
        item for item in service.schedules["os-2026"].lessons if item.id == lesson.id
    )

    assert refreshed.queue.closed is False
    assert refreshed.queue.get_position(github="student") == 1


def test_unknown_lesson_cannot_be_saved(tmp_path):
    service = build(tmp_path)
    with pytest.raises(KeyError):
        service.save_lesson(uuid4())


def test_set_registration_persists_and_is_idempotent(tmp_path):
    first = build(tmp_path)
    lesson = first.schedules["os-2026"].lessons[0]
    assert lesson.queue.closed is True

    opened = first.set_registration(lesson.id, closed=False)
    assert opened.version == 0
    assert first.set_registration(lesson.id, closed=False).version == 0

    second = build(tmp_path)
    restored = next(
        item for item in second.schedules["os-2026"].lessons if item.id == lesson.id
    )
    assert restored.queue.closed is False

    closed = second.set_registration(lesson.id, closed=True)
    assert closed.version == 1
