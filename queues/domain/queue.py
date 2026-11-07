from datetime import datetime
from uuid import UUID, uuid4

from .queue_entry import QueueEntry
from .status import RegistrationMode


class Queue:
    def __init__(
        self,
        *,
        course_id: str = "",
        group_id: str = "",
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        service_until: datetime | None = None,
        id: UUID | None = None,
        lesson_starts_at: datetime | None = None,
        lesson_ends_at: datetime | None = None,
        registration_mode: RegistrationMode = RegistrationMode.AUTOMATIC,
        entries: list[QueueEntry] | None = None,
        version: int = 0,
    ) -> None:
        self.course_id = course_id
        self.group_id = group_id
        self.registration_opens_at = registration_opens_at
        self.registration_closes_at = registration_closes_at
        self.service_until = service_until
        self.id = id if id is not None else uuid4()
        self.lesson_starts_at = lesson_starts_at
        self.lesson_ends_at = lesson_ends_at
        self.registration_mode = registration_mode
        self.version = version
        self.entries: list[QueueEntry] = list(entries) if entries is not None else []

    def add_entry(self, entry: QueueEntry) -> None:
        self.entries.append(entry)
