"""Persistence boundary for the Lesson-based queue service."""
from typing import Protocol

from ...domain.lesson import Lesson


class LessonHistoryRepository(Protocol):
    async def save(
        self,
        lesson: Lesson,
        *,
        course_name: str,
    ) -> bool:
        """Return True for a new snapshot, False for an already archived lesson."""
        ...
