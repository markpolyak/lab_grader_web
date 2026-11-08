"""Преобразование QueueRecord в dict и обратно для файлового хранилища.

Формат фиксирован явно: UUID и datetime не полагаются на автоматическую
сериализацию YAML, а превращаются в строки. Это делает файл стабильным и
позволяет позже заменить хранилище на БД без изменения домена.
"""
from datetime import datetime, timezone
from uuid import UUID

from ....domain.queue import Queue
from ....domain.queue_entry import QueueEntry
from ....domain.queue_record import QueueEntryMetadata, QueueRecord
from ....domain.status import DefenseResult, EntryStatus, RegistrationMode
from ....domain.student import Student


def _datetime_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def student_to_dict(student: Student) -> dict:
    return {
        "name": student.name,
        "surname": student.surname,
        "patronymic": student.patronymic,
        "github": student.github,
    }


def student_from_dict(data: dict) -> Student:
    return Student(**data)


def entry_to_dict(entry: QueueEntry) -> dict:
    return {
        "id": str(entry.id),
        "student": student_to_dict(entry.student),
        "lab_id": entry.lab_id,
        "lab_name": entry.lab_name,
        "enqueued_time": _datetime_to_str(entry.enqueued_time),
    }


def entry_from_dict(data: dict) -> QueueEntry:
    return QueueEntry(
        id=UUID(data["id"]),
        student=student_from_dict(data["student"]),
        lab_id=data["lab_id"],
        lab_name=data.get("lab_name"),
        enqueued_time=(
            _datetime_from_str(data.get("enqueued_time"))
            or datetime.now(timezone.utc)
        ),
    )


def metadata_to_dict(metadata: QueueEntryMetadata) -> dict:
    return {
        "queue_id": str(metadata.queue_id),
        "sequence": metadata.sequence,
        "status": metadata.status.value,
        "called_at": _datetime_to_str(metadata.called_at),
        "finished_at": _datetime_to_str(metadata.finished_at),
        "result": metadata.result.value if metadata.result is not None else None,
    }


def metadata_from_dict(data: dict) -> QueueEntryMetadata:
    result = data.get("result")
    return QueueEntryMetadata(
        queue_id=UUID(data["queue_id"]),
        sequence=data.get("sequence"),
        status=EntryStatus(data.get("status", EntryStatus.WAITING.value)),
        called_at=_datetime_from_str(data.get("called_at")),
        finished_at=_datetime_from_str(data.get("finished_at")),
        result=DefenseResult(result) if result is not None else None,
    )


def record_to_dict(record: QueueRecord) -> dict:
    return {
        "id": str(record.id),
        "course_id": record.course_id,
        "group_id": record.group_id,
        "lesson_starts_at": _datetime_to_str(record.lesson_starts_at),
        "lesson_ends_at": _datetime_to_str(record.lesson_ends_at),
        "registration_opens_at": _datetime_to_str(record.registration_opens_at),
        "registration_closes_at": _datetime_to_str(record.registration_closes_at),
        "service_until": _datetime_to_str(record.service_until),
        "registration_mode": record.registration_mode.value,
        "manual": record.manual,
        "cancelled": record.cancelled,
        "original_starts_at": _datetime_to_str(record.original_starts_at),
        "original_ends_at": _datetime_to_str(record.original_ends_at),
        "lesson_kind": record.lesson_kind,
        "lesson_room": record.lesson_room,
        "version": record.version,
        "queue": {
            "closed": record.queue.closed,
            "entries": [entry_to_dict(entry) for entry in record.queue.entries],
        },
        "entry_metadata": {
            str(entry_id): metadata_to_dict(metadata)
            for entry_id, metadata in record.entry_metadata.items()
        },
    }


def record_from_dict(data: dict) -> QueueRecord:
    if not isinstance(data, dict):
        raise ValueError("Состояние очереди должно быть объектом")
    queue_data = data["queue"]
    if not isinstance(queue_data, dict):
        raise ValueError("Поле queue должно быть объектом")

    queue = Queue(closed=bool(queue_data.get("closed", True)))
    queue.entries = [entry_from_dict(entry) for entry in queue_data.get("entries", [])]

    return QueueRecord(
        id=UUID(data["id"]),
        course_id=data["course_id"],
        group_id=data["group_id"],
        lesson_starts_at=_datetime_from_str(data.get("lesson_starts_at")),
        lesson_ends_at=_datetime_from_str(data.get("lesson_ends_at")),
        registration_opens_at=_datetime_from_str(data.get("registration_opens_at")),
        registration_closes_at=_datetime_from_str(data.get("registration_closes_at")),
        service_until=_datetime_from_str(data.get("service_until")),
        registration_mode=RegistrationMode(
            data.get("registration_mode", RegistrationMode.AUTOMATIC.value)
        ),
        manual=bool(data.get("manual", False)),
        cancelled=bool(data.get("cancelled", False)),
        original_starts_at=_datetime_from_str(data.get("original_starts_at")),
        original_ends_at=_datetime_from_str(data.get("original_ends_at")),
        lesson_kind=data.get("lesson_kind", "Занятие"),
        lesson_room=data.get("lesson_room"),
        version=int(data.get("version", 0)),
        queue=queue,
        entry_metadata={
            UUID(entry_id): metadata_from_dict(metadata)
            for entry_id, metadata in data.get("entry_metadata", {}).items()
        },
    )
