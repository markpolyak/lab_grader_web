from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
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

    def service_deadline(self) -> datetime:
        if self.service_until is not None:
            return self.service_until
        end = self.lesson_ends_at or self.lesson_starts_at
        if end is None:
            raise ValueError("У очереди отсутствует время занятия")
        zone = ZoneInfo("Europe/Moscow")
        return datetime.combine(end.astimezone(zone).date() + timedelta(days=1), time.min, zone)

    def is_open_at(self, now: datetime, *, opens_before_minutes: int = 15) -> bool:
        if now.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        if self.cancelled or self.archived or now >= self.service_deadline():
            return False
        if self.registration_mode == RegistrationMode.MANUALLY_CLOSED:
            return False
        if self.registration_mode == RegistrationMode.MANUALLY_OPENED:
            return True
        opens = self.registration_opens_at or self.lesson_starts_at - timedelta(minutes=opens_before_minutes)
        closes = self.registration_closes_at or self.lesson_ends_at
        return opens <= now < closes
