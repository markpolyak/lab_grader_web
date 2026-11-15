from datetime import date, datetime, timedelta, timezone

from queues.domain import EntryStatus, QueueEntryMetadata, Student
from queues.domain.lesson import Lesson
from queues.domain.schedule import Schedule
from queues.repositories.mappers import (
    ensure_entry_metadata,
    group_ids,
    group_key,
    record_from_lesson,
    restore_record,
    sync_record_with_lesson,
)


def make_lesson(*, groups=("4431",), kind="Лабораторное занятие") -> Lesson:
    start = datetime(2026, 11, 9, 15, 10, tzinfo=timezone(timedelta(hours=3)))
    return Lesson(
        starts_at=start,
        ends_at=start + timedelta(minutes=90),
        groups=groups,
        kind=kind,
        room="23-09",
    )


def make_schedule() -> Schedule:
    return Schedule(
        course_id="os-2026",
        course_name="Операционные системы",
        week_start=date(2026, 11, 9),
    )


def test_group_key_is_sorted_and_deduplicated():
    assert group_key(["4432", "4431", "4431"]) == "4431,4432"
    record = record_from_lesson(make_schedule(), make_lesson(groups=("4432", "4431")))
    assert group_ids(record) == ("4431", "4432")


def test_record_from_lesson_maps_identity_and_keeps_queue_object():
    schedule = make_schedule()
    lesson = make_lesson(groups=("4432", "4431"))

    record = record_from_lesson(schedule, lesson)

    assert record.id == lesson.id
    assert record.course_id == "os-2026"
    assert record.group_id == "4431,4432"
    assert record.lesson_starts_at == lesson.starts_at
    assert record.lesson_ends_at == lesson.ends_at
    assert record.queue is lesson.queue
    assert record.version == 0
    assert record.entry_metadata == {}


def test_sync_record_with_lesson_updates_schedule_fields():
    record = record_from_lesson(make_schedule(), make_lesson())
    other = Schedule(
        course_id="ml-2026",
        course_name="Машинное обучение",
        week_start=date(2026, 11, 9),
    )
    lesson = make_lesson(groups=("9999",))

    sync_record_with_lesson(record, other, lesson)

    assert record.course_id == "ml-2026"
    assert record.group_id == "9999"
    assert record.lesson_starts_at == lesson.starts_at


def test_restore_record_attaches_queue_and_creates_waiting_metadata():
    schedule = make_schedule()
    source = make_lesson()
    record = record_from_lesson(schedule, source)
    record.queue.closed = False
    entry = record.queue.add_entry(
        Student("Иван", "Иванов", "", "student"), "1", "ЛР1"
    )
    target = make_lesson()
    target.id = source.id

    restored = restore_record(record, schedule, target)

    assert restored is record
    assert target.queue is record.queue
    metadata = record.entry_metadata[entry.id]
    assert metadata.queue_id == record.id
    assert metadata.status == EntryStatus.WAITING


def test_ensure_entry_metadata_does_not_overwrite_existing_status():
    record = record_from_lesson(make_schedule(), make_lesson())
    record.queue.closed = False
    entry = record.queue.add_entry(
        Student("Иван", "Иванов", "", "student"), "1", "ЛР1"
    )
    record.entry_metadata[entry.id] = QueueEntryMetadata(
        queue_id=record.id, status=EntryStatus.CALLED
    )

    ensure_entry_metadata(record)

    assert record.entry_metadata[entry.id].status == EntryStatus.CALLED
