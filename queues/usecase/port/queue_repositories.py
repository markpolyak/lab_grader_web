from datetime import date
from typing import Protocol
from uuid import UUID

from ...domain.queue_record import QueueRecord


class QueueRepository(Protocol):
    """Хранилище очередей.

    Интерфейс намеренно синхронный: текущий QueueService и HTTP-слой
    синхронные, а файловый и SQLite-адаптеры не требуют async. Замена
    хранилища (файлы -> SQLite/Postgres) не должна менять вызывающий код.
    """

    def create(self, queue: QueueRecord) -> QueueRecord:
        ...

    def get(self, queue_id: UUID) -> QueueRecord | None:
        ...

    def save(self, queue: QueueRecord, *, expected_version: int) -> QueueRecord:
        ...

    def delete(self, queue_id: UUID) -> None:
        ...

    def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[QueueRecord]:
        ...


class QueueHistoryRepository(Protocol):

    def save_snapshot(self, queue: QueueRecord) -> None:
        ...

    def get(self, queue_id: UUID) -> QueueRecord | None:
        ...

    def list_by_course(
        self,
        course_id: str,
        *,
        group_id: str | None = None,
        lesson_date: date | None = None,
    ) -> list[QueueRecord]:
        ...
