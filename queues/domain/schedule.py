from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

from .lesson import Lesson


@dataclass(kw_only=True)
class Schedule:
    course_id: str
    course_name: str
    week_start: date
    id: UUID = field(default_factory=uuid4)
    lessons: list[Lesson] = field(default_factory=list)
