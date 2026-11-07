from datetime import date
from typing import Protocol
from uuid import UUID

from ...domain.queue import Queue


class QueueRepository(Protocol):

    async def create(self, queue: Queue) -> Queue:
        ...

    async def get(self, queue_id: UUID) -> Queue | None:
        ...

    async def save(self, queue: Queue, *, expected_version: int) -> Queue:
        ...

    async def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[Queue]:
        ...


class QueueHistoryRepository(Protocol):

    async def save_snapshot(self, queue: Queue) -> None:
        ...

    async def get(self, queue_id: UUID) -> Queue | None:
        ...

    async def list_by_course(
        self,
        course_id: str,
        *,
        group_id: str | None = None,
        lesson_date: date | None = None,
    ) -> list[Queue]:
        ...
