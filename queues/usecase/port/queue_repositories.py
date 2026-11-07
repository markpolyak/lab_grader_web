from datetime import date
from typing import Protocol
from uuid import UUID

from ...domain.queue_record import QueueRecord


class QueueRepository(Protocol):

    async def create(self, queue: QueueRecord) -> QueueRecord:
        ...

    async def get(self, queue_id: UUID) -> QueueRecord | None:
        ...

    async def save(self, queue: QueueRecord, *, expected_version: int) -> QueueRecord:
        ...

    async def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[QueueRecord]:
        ...


class QueueHistoryRepository(Protocol):

    async def save_snapshot(self, queue: QueueRecord) -> None:
        ...

    async def get(self, queue_id: UUID) -> QueueRecord | None:
        ...

    async def list_by_course(
        self,
        course_id: str,
        *,
        group_id: str | None = None,
        lesson_date: date | None = None,
    ) -> list[QueueRecord]:
        ...
