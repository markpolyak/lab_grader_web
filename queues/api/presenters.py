"""Сборка HTTP-ответов очереди из доменных объектов.

Ручки в main.py не должны собирать JSON вручную: здесь одно место, где
Lesson, Queue и QueueRecord превращаются в Pydantic-схемы.
"""
from datetime import timedelta

from ..domain.lesson import Lesson
from ..domain.queue_entry import QueueEntry
from ..domain.queue_record import QueueEntryMetadata, QueueRecord
from ..domain.schedule import Schedule
from ..domain.status import EntryStatus
from .schemas import (
    AdminQueueDetailResponse,
    AdminQueueEntryResponse,
    AdminQueueLessonResponse,
    AdminQueuesResponse,
    AdminQueueSummaryResponse,
    QueueEntryActionResponse,
    QueueCreateResponse,
    QueueDeletedResponse,
    QueueEntryRemovedResponse,
    QueueHistoryEntryResponse,
    QueueHistoryResponse,
    QueueRegistrationResponse,
    QueueUpdateResponse,
    StudentQueueEntryResponse,
    StudentQueueStateResponse,
)


def student_full_name(student) -> str:
    return " ".join(
        part for part in (student.surname, student.name, student.patronymic) if part
    )


def queue_entry(
    entry: QueueEntry,
    metadata: QueueEntryMetadata | None,
    *,
    position: int | None = None,
) -> AdminQueueEntryResponse:
    return AdminQueueEntryResponse(
        id=entry.id,
        position=position,
        github=entry.student.github,
        full_name=student_full_name(entry.student),
        lab_id=entry.lab_id,
        lab_name=entry.lab_name,
        status=metadata.status if metadata is not None else EntryStatus.WAITING,
        joined_at=entry.enqueued_time,
        called_at=metadata.called_at if metadata is not None else None,
        finished_at=metadata.finished_at if metadata is not None else None,
        result=metadata.result if metadata is not None else None,
    )


def queue_lesson(lesson: Lesson) -> AdminQueueLessonResponse:
    return AdminQueueLessonResponse(
        id=lesson.id,
        starts_at=lesson.starts_at,
        ends_at=lesson.ends_at,
        groups=list(lesson.groups),
        kind=lesson.kind,
        room=lesson.room,
    )


def queue_summary(lesson: Lesson) -> AdminQueueSummaryResponse:
    return AdminQueueSummaryResponse(
        queue_id=lesson.id,
        lesson=queue_lesson(lesson),
        closed=lesson.queue.closed,
        entries_count=len(lesson.queue.entries),
    )


def queues_response(
    schedule: Schedule, lessons: list[Lesson]
) -> AdminQueuesResponse:
    return AdminQueuesResponse(
        course_id=schedule.course_id,
        course_name=schedule.course_name,
        week_start=schedule.week_start,
        week_end=schedule.week_start + timedelta(days=6),
        queues=[queue_summary(lesson) for lesson in lessons],
    )


def queue_detail(
    schedule: Schedule, lesson: Lesson, record: QueueRecord | None
) -> AdminQueueDetailResponse:
    metadata = record.entry_metadata if record is not None else {}
    waiting: list[AdminQueueEntryResponse] = []
    called: list[AdminQueueEntryResponse] = []
    finished: list[AdminQueueEntryResponse] = []

    for entry in lesson.queue.entries:
        meta = metadata.get(entry.id)
        status = meta.status if meta is not None else EntryStatus.WAITING
        if status == EntryStatus.WAITING:
            waiting.append(queue_entry(entry, meta, position=len(waiting) + 1))
        elif status == EntryStatus.CALLED:
            called.append(queue_entry(entry, meta))
        else:
            # finished / withdrawn / expired — терминальные состояния для админки
            finished.append(queue_entry(entry, meta))

    return AdminQueueDetailResponse(
        queue_id=lesson.id,
        course_id=schedule.course_id,
        lesson=queue_lesson(lesson),
        closed=lesson.queue.closed,
        version=record.version if record is not None else 0,
        waiting=waiting,
        called=called,
        finished=finished,
    )


def registration_response(
    lesson: Lesson, record: QueueRecord
) -> QueueRegistrationResponse:
    return QueueRegistrationResponse(
        queue_id=lesson.id,
        closed=lesson.queue.closed,
        version=record.version,
    )


def queue_create_response(record: QueueRecord) -> QueueCreateResponse:
    return QueueCreateResponse(queue_id=record.id, version=record.version)


def queue_update_response(record: QueueRecord) -> QueueUpdateResponse:
    return QueueUpdateResponse(queue_id=record.id, version=record.version)


def queue_deleted_response() -> QueueDeletedResponse:
    return QueueDeletedResponse(deleted=True)


def entry_removed_response() -> QueueEntryRemovedResponse:
    return QueueEntryRemovedResponse(removed=True)


def waiting_position(record: QueueRecord, entry_id) -> int | None:
    position = 0
    for entry in record.queue.entries:
        metadata = record.entry_metadata.get(entry.id)
        status = metadata.status if metadata is not None else EntryStatus.WAITING
        if status == EntryStatus.WAITING:
            position += 1
            if entry.id == entry_id:
                return position
    return None


def student_queue_entry(
    entry: QueueEntry,
    metadata: QueueEntryMetadata | None,
    *,
    lesson_id,
    position: int | None = None,
) -> StudentQueueEntryResponse:
    return StudentQueueEntryResponse(
        id=entry.id,
        lesson_id=lesson_id,
        lab_id=entry.lab_id,
        lab_name=entry.lab_name,
        status=metadata.status if metadata is not None else EntryStatus.WAITING,
        position=position,
        joined_at=entry.enqueued_time,
        called_at=metadata.called_at if metadata is not None else None,
    )


def student_queue_state(
    *,
    github: str,
    group_id: str,
    full_name: str,
    entry: StudentQueueEntryResponse | None,
) -> StudentQueueStateResponse:
    return StudentQueueStateResponse(
        github=github,
        group_id=group_id,
        full_name=full_name,
        entry=entry,
    )


def entry_action_response(
    entry: QueueEntry,
    metadata: QueueEntryMetadata | None,
    version: int,
) -> QueueEntryActionResponse:
    return QueueEntryActionResponse(
        entry=queue_entry(entry, metadata),
        version=version,
    )


def history_entry(item: dict) -> QueueHistoryEntryResponse:
    return QueueHistoryEntryResponse(**item)


def history_response(
    items: list[dict], next_cursor: str | None
) -> QueueHistoryResponse:
    return QueueHistoryResponse(
        entries=[history_entry(item) for item in items],
        next_cursor=next_cursor,
    )
