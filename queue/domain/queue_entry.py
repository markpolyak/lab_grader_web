from dataclasses import dataclass
from datetime import datetime

from .student import Student


@dataclass
class QueueEntry:
    student: Student
    lab_id: str
    enqueued_time: datetime
    lab_name: str | None = None
