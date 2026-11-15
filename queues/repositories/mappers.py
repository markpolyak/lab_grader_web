"""Mappers между расписанием (Lesson) и хранимым состоянием (QueueRecord).

Домен расписания и домен хранения намеренно не знают друг о друге:
Lesson описывает занятие, QueueRecord — сохраняемую очередь. Этот модуль —
единственное место, где эти две модели встречаются.
"""
from ..domain.lesson import Lesson
from ..domain.queue import Queue
from ..domain.queue_record import QueueEntryMetadata, QueueRecord
from ..domain.schedule import Schedule


def group_key(groups) -> str:
    """Канонический ключ групп занятия: отсортированные уникальные значения."""
    return ",".join(sorted({str(group) for group in groups}))


def group_ids(record: QueueRecord) -> tuple[str, ...]:
    return tuple(record.group_id.split(",")) if record.group_id else ()


def record_from_lesson(
    schedule: Schedule, lesson: Lesson, *, queue: Queue | None = None
) -> QueueRecord:
    """Новый QueueRecord для занятия; версия 0, ещё не сохранён."""
    return QueueRecord(
        id=lesson.id,
        course_id=schedule.course_id,
        group_id=group_key(lesson.groups),
        lesson_starts_at=lesson.starts_at,
        lesson_ends_at=lesson.ends_at,
        lesson_kind=lesson.kind,
        lesson_room=lesson.room,
        cancelled=lesson.cancelled,
        original_starts_at=lesson.original_starts_at,
        queue=queue if queue is not None else lesson.queue,
    )


def sync_record_with_lesson(
    record: QueueRecord, schedule: Schedule, lesson: Lesson
) -> QueueRecord:
    """Обновить поля записи, которые принадлежат расписанию, а не очереди."""
    record.course_id = schedule.course_id
    record.group_id = group_key(lesson.groups)
    record.lesson_starts_at = lesson.starts_at
    record.lesson_ends_at = lesson.ends_at
    record.lesson_kind = lesson.kind
    record.lesson_room = lesson.room
    record.cancelled = lesson.cancelled
    record.original_starts_at = lesson.original_starts_at
    return record


def restore_record(
    record: QueueRecord, schedule: Schedule, lesson: Lesson
) -> QueueRecord:
    """Восстановить сохранённую очередь в занятии.

    Lesson получает тот же объект Queue, что и запись, поэтому дальнейшие
    изменения видны и очереди, и снимку для сохранения.
    """
    lesson.queue = record.queue
    lesson.cancelled = record.cancelled
    lesson.original_starts_at = record.original_starts_at
    if record.manual or record.original_starts_at is not None:
        lesson.starts_at = record.lesson_starts_at
        lesson.ends_at = record.lesson_ends_at
    if lesson.cancelled:
        lesson.queue.closed = True
    ensure_entry_metadata(record)
    return sync_record_with_lesson(record, schedule, lesson)


def ensure_entry_metadata(record: QueueRecord) -> QueueRecord:
    """У каждой записи должна быть метадата; новые считаются ожидающими."""
    for entry in record.queue.entries:
        record.entry_metadata.setdefault(
            entry.id, QueueEntryMetadata(queue_id=record.id)
        )
    return record
