"""Persistence orchestration; QueueService owns only the in-memory state."""
import logging
from threading import RLock
from uuid import UUID
from collections.abc import Mapping

from .port.lesson_history import LessonHistoryRepository
from .queue_service import QueueService

logger = logging.getLogger(__name__)


class QueueHistoryService:
    def __init__(
        self,
        queues: QueueService,
        history: LessonHistoryRepository | None = None,
        *,
        history_by_course: Mapping[str, LessonHistoryRepository] | None = None,
    ) -> None:
        self._queues = queues
        self._history = history
        self._history_by_course = dict(history_by_course or {})
        self._saving: set[UUID] = set()
        self._history_retries: set[UUID] = set()
        self._lock = RLock()

    async def save_history(self, schedule_id: UUID, lesson_id: UUID) -> None:
        with self._lock:
            if lesson_id in self._saving:
                raise RuntimeError("Очередь уже сохраняется")
            context = self._queues.begin_archive(schedule_id, lesson_id)
            if context is None:
                return
            course_id, course_name, snapshot = context
            history = self._history_by_course.get(course_id, self._history)
            if history is None:
                self._queues.cancel_archive(lesson_id)
                raise RuntimeError("Не настроена таблица истории очередей")
            self._saving.add(lesson_id)
        try:
            # Neither service holds a lock while Google is responding.
            await history.save(snapshot, course_name=course_name)
            self._queues.complete_archive(lesson_id)
        except BaseException:
            self._queues.cancel_archive(lesson_id)
            with self._lock:
                self._history_retries.add(lesson_id)
            raise
        else:
            with self._lock:
                self._history_retries.discard(lesson_id)
        finally:
            with self._lock:
                self._saving.discard(lesson_id)

    async def archive_finished(self, *, retry_only: bool = False) -> dict[UUID, Exception]:
        candidates = self._queues.finished_lessons()
        with self._lock:
            candidates = [(sid, lid) for sid, lid in candidates
                          if lid not in self._saving
                          and (not retry_only or lid in self._history_retries)]
        errors = {}
        for schedule_id, lesson_id in candidates:
            try:
                await self.save_history(schedule_id, lesson_id)
            except Exception as exc:
                errors[lesson_id] = exc
                logger.exception("Не удалось сохранить историю занятия %s", lesson_id)
        return errors
