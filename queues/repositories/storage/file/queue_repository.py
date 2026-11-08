"""Файловое хранилище очередей.

Один YAML-файл на очередь в каталоге QUEUE_STATE_DIR (по умолчанию
``queue_state``). Запись атомарная: временный файл в том же каталоге +
``os.replace``. Каталог расписания и каталог состояния очередей не
пересекаются, поэтому парсер расписания и очередь не пишут в один файл.

Адаптер рассчитан на один процесс backend (как и остальное приложение).
Для нескольких процессов нужен адаптер на БД с тем же интерфейсом
QueueRepository.
"""
import logging
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import UUID

import yaml

from ....domain.err import (
    QueueAlreadyExistsError,
    QueueError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from ....domain.queue_record import QueueRecord
from .serialization import record_from_dict, record_to_dict

logger = logging.getLogger(__name__)


class QueueStorageCorruptedError(QueueError):
    """Файл состояния очереди не удалось прочитать."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Файл состояния очереди повреждён: {path}")


class FileQueueRepository:

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(
            directory or os.getenv("QUEUE_STATE_DIR", "queue_state")
        )
        self._lock = RLock()

    # -- QueueRepository -------------------------------------------------

    def create(self, queue: QueueRecord) -> QueueRecord:
        if queue.version != 0:
            raise ValueError("Новая очередь должна иметь версию 0")
        with self._lock:
            path = self._path(queue.id)
            if path.exists():
                raise QueueAlreadyExistsError(queue.id)
            self._write(path, queue)
            return deepcopy(queue)

    def get(self, queue_id: UUID) -> QueueRecord | None:
        with self._lock:
            path = self._path(queue_id)
            if not path.exists():
                return None
            return self._read(path)

    def save(self, queue: QueueRecord, *, expected_version: int) -> QueueRecord:
        if queue.version != expected_version:
            raise ValueError("Версия снимка должна совпадать с expected_version")
        with self._lock:
            path = self._path(queue.id)
            if not path.exists():
                raise QueueNotFoundError(queue.id)
            current = self._read(path)
            if current.version != expected_version:
                raise QueueVersionConflictError(
                    queue.id, expected_version, current.version
                )
            stored = deepcopy(queue)
            stored.version = current.version + 1
            self._write(path, stored)
            return deepcopy(stored)

    def delete(self, queue_id: UUID) -> None:
        with self._lock:
            path = self._path(queue_id)
            if not path.exists():
                raise QueueNotFoundError(queue_id)
            path.unlink()

    def list_by_course(
        self, course_id: str, *, group_id: str | None = None
    ) -> list[QueueRecord]:
        with self._lock:
            records: list[QueueRecord] = []
            for path in sorted(self.directory.glob("*.yaml")):
                try:
                    record = self._read(path)
                except QueueStorageCorruptedError:
                    # One broken file must not hide the rest of the course.
                    logger.warning("Пропускаю повреждённый файл очереди: %s", path)
                    continue
                if record.course_id == course_id and (
                    group_id is None or record.group_id == group_id
                ):
                    records.append(record)
            return records

    # -- внутреннее ------------------------------------------------------

    def _path(self, queue_id: UUID) -> Path:
        return self.directory / f"{queue_id}.yaml"

    def _read(self, path: Path) -> QueueRecord:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Состояние очереди должно быть объектом")
            return record_from_dict(data)
        except Exception as exc:
            self._quarantine(path)
            raise QueueStorageCorruptedError(path) from exc

    def _write(self, path: Path, record: QueueRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                yaml.safe_dump(
                    record_to_dict(record),
                    stream,
                    allow_unicode=True,
                    sort_keys=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _quarantine(self, path: Path) -> None:
        """Убрать повреждённый файл в сторону, чтобы следующая запись была чистой."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, target)
        except OSError:
            logger.exception("Не удалось отложить повреждённый файл %s", path)
            return
        logger.error("Повреждённый файл очереди отложен: %s -> %s", path, target)
