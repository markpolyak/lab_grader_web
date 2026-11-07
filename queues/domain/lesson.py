from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from .queue import Queue


@dataclass(kw_only=True)
class Lesson:
    starts_at: datetime
    ends_at: datetime
    groups: tuple[str, ...]
    kind: str
    room: str | None = None
    id: UUID = field(default_factory=uuid4)
    queue: Queue = field(default_factory=Queue)

    def __post_init__(self):
        if any(value.utcoffset() is None for value in (self.starts_at, self.ends_at)):
            raise ValueError("Время занятия должно содержать часовой пояс")
        if self.ends_at <= self.starts_at:
            raise ValueError("Окончание занятия должно быть позже начала")
