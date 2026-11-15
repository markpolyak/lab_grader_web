from copy import deepcopy
from threading import Lock
from uuid import UUID

from ....domain.err import (
    QueueAlreadyExistsError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from ....domain.queue_record import QueueRecord


class InMemoryQueueRepository:

    def __init__(self) -> None:
        self._queues: dict[UUID, QueueRecord] = {}
        self._lock = Lock()

    def create(self, queue: QueueRecord) -> QueueRecord:
        if queue.version != 0:
            raise ValueError("Новая очередь должна иметь версию 0")
        with self._lock:
            if queue.id in self._queues:
                raise QueueAlreadyExistsError(queue.id)
            stored = deepcopy(queue)
            self._queues[queue.id] = stored
            return deepcopy(stored)

    def get(self, queue_id: UUID) -> QueueRecord | None:
        with self._lock:
            return deepcopy(self._queues.get(queue_id))

    def save(self, queue: QueueRecord, *, expected_version: int) -> QueueRecord:
        if queue.version != expected_version:
            raise ValueError("Версия снимка должна совпадать с expected_version")
        with self._lock:
            current = self._queues.get(queue.id)
            if current is None:
                raise QueueNotFoundError(queue.id)
            if current.version != expected_version:
                raise QueueVersionConflictError(
                    queue.id, expected_version, current.version
                )
            stored = deepcopy(queue)
            stored.version = current.version + 1
            self._queues[queue.id] = stored
            return deepcopy(stored)

    def delete(self, queue_id: UUID) -> None:
        with self._lock:
            if self._queues.pop(queue_id, None) is None:
                raise QueueNotFoundError(queue_id)

    def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[QueueRecord]:
        with self._lock:
            return [
                deepcopy(queue)
                for queue in self._queues.values()
                if queue.course_id == course_id
                and (group_id is None or queue.group_id == group_id)
            ]
