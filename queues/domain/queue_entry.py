from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .student import Student


@dataclass
class QueueEntry:
    student: Student
    lab_id: str
    enqueued_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lab_name: str | None = None
    id: UUID = field(default_factory=uuid4)
