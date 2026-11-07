from dataclasses import dataclass, field
from datetime import date

from .lesson import Lesson


@dataclass(kw_only=True)
class Schedule:
    course_id: str
    course_name: str
    week_start: date
    lessons: list[Lesson] = field(default_factory=list)
