from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from .queue import Queue
from .status import DefenseResult, EntryStatus, RegistrationMode


@dataclass(slots=True, kw_only=True)
class QueueEntryMetadata:
    queue_id: UUID
    sequence: int | None = None
    status: EntryStatus = EntryStatus.WAITING
    called_at: datetime | None = None
    finished_at: datetime | None = None
    result: DefenseResult | None = None


@dataclass(slots=True, kw_only=True)
class QueueRecord:
    course_id: str
    group_id: str
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    service_until: datetime | None = None
    id: UUID = field(default_factory=uuid4)
    lesson_starts_at: datetime | None = None
    lesson_ends_at: datetime | None = None
    registration_mode: RegistrationMode = RegistrationMode.AUTOMATIC
    manual: bool = False
    cancelled: bool = False
    archive_requested: bool = False
    archived: bool = False
    original_starts_at: datetime | None = None
    original_ends_at: datetime | None = None
    lesson_kind: str = "Занятие"
    lesson_room: str | None = None
    queue: Queue = field(default_factory=Queue)
    entry_metadata: dict[UUID, QueueEntryMetadata] = field(default_factory=dict)
    version: int = 0
