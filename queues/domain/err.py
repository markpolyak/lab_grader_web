from uuid import UUID


class QueueError(Exception):
    pass


class QueueClosedError(QueueError):
    def __init__(self) -> None:
        super().__init__("Запись в очередь закрыта")


class QueueEmptyError(QueueError):
    def __init__(self) -> None:
        super().__init__("В очереди нет ожидающих")


class EntryNotFoundError(QueueError):
    def __init__(self, entry_id: UUID | None = None) -> None:
        self.entry_id = entry_id
        super().__init__("Запись в очереди не найдена")


class EntryNotOwnedError(QueueError):
    def __init__(self) -> None:
        super().__init__("Запись принадлежит другому студенту")


class InvalidStateError(QueueError):
    def __init__(self, message: str = "Недопустимое состояние записи") -> None:
        super().__init__(message)


class AlreadyQueuedError(QueueError):
    def __init__(self, entry_id: UUID | None = None) -> None:
        self.entry_id = entry_id
        super().__init__("Студент уже стоит в очереди")


class GroupNotAllowedError(QueueError):
    def __init__(self) -> None:
        super().__init__("Группа студента не записана на это занятие")


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
