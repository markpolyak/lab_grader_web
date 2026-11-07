from copy import deepcopy
from threading import Lock
from uuid import UUID

from ....domain.err import (
    QueueAlreadyExistsError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from ....domain.queue import Queue


class InMemoryQueueRepository:

    def __init__(self) -> None:
        self._queues: dict[UUID, Queue] = {}
        self._lock = Lock()

    async def create(self, queue: Queue) -> Queue:
        if queue.version != 0:
            raise ValueError("Новая очередь должна иметь версию 0")
        with self._lock:
            if queue.id in self._queues:
                raise QueueAlreadyExistsError(queue.id)
            stored = deepcopy(queue)
            self._queues[queue.id] = stored
            return deepcopy(stored)

    async def get(self, queue_id: UUID) -> Queue | None:
        with self._lock:
            return deepcopy(self._queues.get(queue_id))

    async def save(self, queue: Queue, *, expected_version: int) -> Queue:
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

    async def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[Queue]:
        with self._lock:
            return [
                deepcopy(queue)
                for queue in self._queues.values()
                if queue.course_id == course_id
                and (group_id is None or queue.group_id == group_id)
            ]
