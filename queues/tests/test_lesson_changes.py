from datetime import datetime, timedelta

import pytest

from queues.domain.err import InvalidStateError
from queues.domain.student import Student
from queues.tests.test_queue_management import build, COURSES, SCHEDULE_DIR, MONDAY, TZ


def test_cancel_keeps_students_and_survives_restart(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]
    service.set_registration(lesson.id, closed=False)
    entry = lesson.queue.add_entry(Student("Иван", "Иванов", "", "demo"), "1")
    service.save_lesson(lesson.id)
    service.update_queue(lesson.id, cancelled=True)
    with pytest.raises(InvalidStateError):
        service.set_registration(lesson.id, closed=False)
    restored = build(tmp_path)
    item = restored.get_lesson("os-2026", lesson.id)
    assert item.cancelled and item.queue.closed
    assert item.queue.entries[0].id == entry.id
    restored.update_queue(item.id, cancelled=False)
    restored.set_registration(item.id, closed=False)
    assert not item.cancelled and not item.queue.closed


def test_move_to_another_week_preserves_id_queue_and_source_marker(tmp_path):
    service = build(tmp_path)
    original = service.schedules["os-2026"].lessons[0]
    service.set_registration(original.id, closed=False)
    entry = original.queue.add_entry(Student("Иван", "Иванов", "", "demo"), "1")
    service.save_lesson(original.id)
    new_start = original.starts_at + timedelta(days=7)
    new_end = original.ends_at + timedelta(days=7)
    service.update_queue(original.id, starts_at=new_start, ends_at=new_end)
    assert all(item.id != original.id for item in service.schedules["os-2026"].lessons)
    assert original.id in [item.id for item in service.admin_lessons("os-2026", MONDAY)]
    restored = build(tmp_path)
    assert restored.get_lesson("os-2026", original.id).starts_at == new_start
    restored.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY + timedelta(days=7))
    item = next(item for item in restored.schedules["os-2026"].lessons if item.id == original.id)
    assert item.starts_at == new_start
    assert item.kind == "Лабораторное занятие"
    assert item.room == original.room
    assert item.queue.entries[0].id == entry.id
    assert item.original_starts_at == original.original_starts_at

    # Returning a lesson to the source week updates the public list immediately.
    service.update_queue(original.id, starts_at=original.original_starts_at, ends_at=new_end - timedelta(days=7))
    assert original.id in [item.id for item in service.schedules["os-2026"].lessons]
    assert original.original_starts_at is None


def test_invalid_move_does_not_change_existing_state(tmp_path):
    service = build(tmp_path)
    lesson = service.schedules["os-2026"].lessons[0]
    old_start, old_end = lesson.starts_at, lesson.ends_at
    with pytest.raises(ValueError):
        service.update_queue(lesson.id, starts_at=datetime(2026, 11, 10, 17, tzinfo=TZ))
    assert (lesson.starts_at, lesson.ends_at) == (old_start, old_end)
    assert lesson.original_starts_at is None
    assert service.get_record(lesson.id).version == 0
