from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from .status import DefenseResult, EntryStatus
from .student import Student


@dataclass
class QueueEntry:
    student: Student
    lab_id: str
    enqueued_time: datetime
    lab_name: str | None = None
    queue_id: UUID | None = None
    sequence: int | None = None
    id: UUID = field(default_factory=uuid4)
    status: EntryStatus = EntryStatus.WAITING
    called_at: datetime | None = None
    finished_at: datetime | None = None
    result: DefenseResult | None = None
