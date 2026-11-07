from uuid import UUID


class QueueError(Exception):
    pass


class QueueAlreadyExistsError(QueueError):
    def __init__(self, queue_id: UUID) -> None:
        self.queue_id = queue_id
        super().__init__(f"Очередь {queue_id} уже существует")


class QueueNotFoundError(QueueError):
    def __init__(self, queue_id: UUID) -> None:
        self.queue_id = queue_id
        super().__init__(f"Очередь {queue_id} не найдена")


class QueueVersionConflictError(QueueError):
    def __init__(
        self, queue_id: UUID, expected_version: int, actual_version: int
    ) -> None:
        self.queue_id = queue_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Очередь {queue_id} изменилась: ожидалась версия "
            f"{expected_version}, текущая версия {actual_version}"
        )
